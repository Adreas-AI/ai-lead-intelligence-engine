"""End-to-end API tests: import -> enrich -> score -> draft -> review -> export -> analytics."""

import csv
import io

import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.providers.crm import CrmError, CrmProvider
from app.services.crm import CrmSyncService

pytestmark = pytest.mark.integration


def _import(client, demo_csv):
    r = client.post("/leads/import", files={"file": ("demo.csv", demo_csv, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["llm_provider"] == "mock"
    assert body["integrations"] == {"openai": False, "google_places": False, "hubspot": False}


def test_full_pipeline(client, demo_csv, settings):
    report = _import(client, demo_csv)
    assert (
        report["created"] == 24 and len(report["duplicates"]) == 3 and len(report["invalid"]) == 2
    )

    r = client.post("/pipeline/run", json={"use_llm": True})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["enrichment"]["processed"] == 24
    assert run["enrichment"]["failed"] == 1  # the unreachable synthetic site
    assert run["scoring"]["succeeded"] == 24
    assert run["outreach"]["succeeded"] >= 10

    leads = client.get("/leads", params={"qualification": "qualified"}).json()
    assert leads["total"] == run["outreach"]["processed"]
    names = {lead["name"] for lead in leads["items"]}
    assert "Aegean Citrus Growers Cooperative" in names
    assert not names & {
        "Corner Deli Athens",
        "Taverna Thalassa",
        "AgriCloud Analytics",
        "Olympus Agro Supplies",
        "Golden State Almond Growers",
    }

    # Explainable, structured score on the detail endpoint.
    top = leads["items"][0]
    detail = client.get(f"/leads/{top['id']}").json()
    score = detail["latest_score"]
    assert {"score", "reasoning", "confidence", "status", "breakdown"} <= score.keys()
    assert len(score["breakdown"]) == 6
    assert detail["enrichment"]["website_verified"] is True
    assert detail["enrichment"]["source_urls"]
    assert detail["drafts"][0]["status"] == "pending_review"

    # The mismatched website is not treated as official.
    olympus = client.get("/leads", params={"q": "Olympus Agro"}).json()["items"][0]
    olympus_detail = client.get(f"/leads/{olympus['id']}").json()
    assert olympus_detail["enrichment"]["website_verified"] is False
    assert "website_verification" in olympus_detail["enrichment"]["missing_fields"]

    # Human review of a draft.
    draft_id = detail["drafts"][0]["id"]
    r = client.patch(
        f"/outreach/drafts/{draft_id}", json={"status": "approved", "reviewer_notes": "Looks good"}
    )
    assert r.json()["status"] == "approved" and r.json()["reviewed_at"]

    # CSV export of qualified leads.
    r = client.post("/crm/export/csv", json={})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == leads["total"] == int(r.headers["X-Exported-Rows"])
    assert all(row["qualification_status"] == "qualified" for row in rows)
    assert list(settings.export_dir.glob("qualified_leads_*.csv"))
    # only_new exports nothing the second time
    assert client.post("/crm/export/csv", json={"only_new": True}).headers["X-Exported-Rows"] == "0"

    stats = client.get("/analytics/summary").json()
    assert stats["total_leads"] == 24
    assert stats["qualified_leads"] == leads["total"]
    assert stats["duplicates_blocked"] == 3
    assert stats["drafts_approved"] == 1
    assert stats["average_score"] is not None
    assert any(c["provider"] == "mock" for c in stats["costs"])


def test_rerunning_pipeline_is_idempotent(client, demo_csv):
    _import(client, demo_csv)
    client.post("/pipeline/run", json={})
    again = client.post("/pipeline/run", json={}).json()
    assert again["enrichment"]["processed"] == 0
    assert again["scoring"]["processed"] == 0
    assert again["outreach"]["processed"] == 0


def test_single_lead_scoring_returns_structured_json(client):
    r = client.post(
        "/leads",
        json=[
            {
                "name": "Crete Avocado Growers",
                "country": "GR",
                "website": "https://crete-avocado.example",
                "industry": "Agriculture",
                "company_type": "Grower",
            }
        ],
    )
    company_id = r.json()["created_ids"][0]
    assert client.post(f"/leads/{company_id}/enrich").json()["status"] == "success"
    score = client.post(f"/leads/{company_id}/score").json()
    assert score["status"] == "qualified"
    assert 0 <= score["score"] <= 100 and 0 <= score["confidence"] <= 1


def test_discovery_mock_saves_and_deduplicates(client, demo_csv):
    _import(client, demo_csv)
    r = client.post("/discovery/search", json={"provider": "mock", "limit": 50})
    body = r.json()
    assert body["found"] == 7
    assert body["import_report"]["created"] == 6
    assert body["import_report"]["duplicates"][0]["reason"] == "same_domain"
    providers = {p["name"]: p for p in client.get("/discovery/providers").json()}
    assert providers["google_places"]["configured"] is False
    assert client.post("/discovery/search", json={"provider": "google_places"}).status_code == 503
    assert client.post("/discovery/search", json={"provider": "nope"}).status_code == 422


def test_validation_errors(client):
    assert client.post("/leads", json=[{"name": "A"}]).status_code == 422
    assert client.post("/leads", json=[{"name": "Ok Co", "country": "Narnia"}]).status_code == 422
    assert client.post("/discovery/search", json={"countries": ["Narnia"]}).status_code == 422
    assert client.get("/leads/999").status_code == 404
    bad = client.post("/leads/import", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert bad.status_code == 422


def test_hubspot_sync_without_token_is_unavailable(client):
    assert client.post("/crm/sync", json={}).status_code == 503


class FakeCrm(CrmProvider):
    name = "hubspot"

    def __init__(self, fail_for=()):
        self.records, self.fail_for, self.creates = {}, set(fail_for), 0

    def find_by_domain(self, domain):
        return next((k for k, v in self.records.items() if v.get("domain") == domain), None)

    def create_company(self, properties):
        if properties["name"] in self.fail_for:
            raise CrmError("HubSpot POST -> 400: invalid property")
        self.creates += 1
        external_id = str(len(self.records) + 1)
        self.records[external_id] = properties
        return external_id

    def update_company(self, external_id, properties):
        self.records[external_id] = properties


def test_crm_sync_prevents_duplicates_and_records_failures(client, demo_csv, db):
    _import(client, demo_csv)
    client.post("/pipeline/run", json={"draft": False})
    crm = FakeCrm(fail_for={"Berlin Organic Wholesale GmbH"})
    first = CrmSyncService(db, crm).run_batch()
    assert first.failed == 1 and first.succeeded == first.processed - 1
    creates = crm.creates

    second = CrmSyncService(db, crm).run_batch()  # re-sync: updates, never re-creates
    assert crm.creates == creates
    assert len(crm.records) == creates

    failed = client.get("/crm/sync-records", params={"status": "failed"}).json()
    assert len(failed) == 1 and failed[0]["attempts"] == 2 and "400" in failed[0]["error"]
    assert second.failed == 1
    assert client.get("/analytics/summary").json()["crm_failures"] == 1


def test_api_key_and_rate_limit(engine, settings):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.main import create_app

    secured = settings.model_copy(update={"api_key": SecretStr("s3cret")})
    get_settings.cache_clear()
    import os

    os.environ["API_RATE_LIMIT_PER_MINUTE"] = "3"
    try:
        app = create_app()
    finally:
        os.environ["API_RATE_LIMIT_PER_MINUTE"] = "10000"
        get_settings.cache_clear()
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    app.dependency_overrides[deps.get_db] = lambda: (yield factory())
    app.dependency_overrides[get_settings] = lambda: secured
    with TestClient(app) as c:
        assert c.get("/leads").status_code == 401
        assert c.get("/leads", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/leads", headers={"X-API-Key": "s3cret"}).status_code == 200
        r = c.get("/leads", headers={"X-API-Key": "s3cret"})
        assert r.status_code == 429 and "Retry-After" in r.headers
        assert c.get("/health").status_code == 200  # health is exempt
