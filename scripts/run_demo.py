"""Reproducible end-to-end demo on SYNTHETIC data, driven through the REST API.

    python scripts/run_demo.py                 # in-process API against DATABASE_URL
    python scripts/run_demo.py --api http://localhost:8000
    python scripts/run_demo.py --reset --use-llm

Steps: understand free-text searches -> import CSVs for three example profiles
(restaurants, hair salons, agri-food B2B) -> detect duplicates -> enrich -> score ->
draft outreach -> export qualified leads -> pipeline analytics.

The demo never touches the network: companies are fictional (*.example domains)
and their websites are local fixtures. For a real search, see the README.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEMO_DIR = ROOT / "data" / "demo"
DEMO_PROFILES = ("restaurants", "hair_salons", "agrifood_b2b")
EXAMPLE_SEARCHES = (
    ("κρεοπωλεία στην Αθήνα", None),
    ("dental clinics", "Thessaloniki"),
    ("αγροτικά μηχανήματα", None),
)


def header(n: int, title: str) -> None:
    print(f"\n{'=' * 78}\n {n}. {title}\n{'=' * 78}")


def make_client(api: str | None):
    if api:
        import httpx

        headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}
        return httpx.Client(base_url=api, headers=headers, timeout=300)
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "")})


def reset_synthetic() -> None:
    """Delete only synthetic demo leads (real leads are never touched)."""
    from sqlalchemy import delete, or_

    from app.db.models import Company, ImportBatch
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        removed = db.execute(delete(Company).where(Company.is_synthetic.is_(True))).rowcount
        labels = [f"{p}.csv" for p in DEMO_PROFILES]
        db.execute(
            delete(ImportBatch).where(
                or_(ImportBatch.label.in_(labels), ImportBatch.label == "discovery:mock")
            )
        )
        db.commit()
    print(f"reset: removed {removed} synthetic leads")


def ensure_schema() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--api", help="base URL of a running API (default: in-process)")
    parser.add_argument("--reset", action="store_true", help="remove previous synthetic demo leads")
    parser.add_argument(
        "--use-llm", action="store_true", help="enable the LLM layer (LLM_PROVIDER)"
    )
    args = parser.parse_args()

    if not args.api:
        os.chdir(ROOT)
        ensure_schema()
        if args.reset:
            reset_synthetic()
    client = make_client(args.api)

    def call(method: str, path: str, **kwargs):
        r = client.request(method, path, **kwargs)
        if r.status_code >= 400:
            sys.exit(f"{method} {path} failed: {r.status_code} {r.text}")
        return r

    print("AI Lead Intelligence Engine - demo on SYNTHETIC data (fictional .example companies)")

    header(1, "Understand free-text searches (preview only, nothing is searched)")
    for query, city in EXAMPLE_SEARCHES:
        plan = call(
            "POST", "/search/interpret", json={"query": query, "country": "GR", "city": city}
        ).json()
        print(f"\n  You typed: {query!r}" + (f"  (city: {city})" if city else ""))
        print(f"  Method:    {plan['method']}")
        print(f"  Understood: {plan['explanation']}")
        if plan["osm_tags"]:
            print(f"  OpenStreetMap tags: {', '.join(plan['osm_tags'])}")
        if plan["name_keywords"]:
            print(f"  Name keywords: {', '.join(plan['name_keywords'])}")
        for w in plan["warnings"]:
            print(f"  ! {w}")

    header(2, "Import leads from CSV, one dataset per profile")
    reports = {}
    for profile in DEMO_PROFILES:
        with (DEMO_DIR / f"{profile}.csv").open("rb") as fh:
            rep = call(
                "POST",
                "/leads/import",
                data={"profile": profile},
                files={"file": (f"{profile}.csv", fh, "text/csv")},
            ).json()
        reports[profile] = rep
        print(
            f"  {profile:<14} rows {rep['total_rows']:>2}  created {rep['created']:>2}  "
            f"duplicates {len(rep['duplicates'])}  invalid {len(rep['invalid'])}"
        )
        for err in rep["invalid"]:
            print(f"      invalid row {err['row']}: {err['error']}")

    header(3, "Duplicate detection")
    for profile, rep in reports.items():
        for d in rep["duplicates"]:
            print(
                f"  [{profile}] '{d['name']}' -> #{d['matched_company_id']} "
                f"'{d['matched_name']}' ({d['reason']}, similarity {d['similarity']})"
            )
    disc = call(
        "POST",
        "/discovery/search",
        json={"provider": "mock", "profile": "agrifood_b2b", "limit": 50},
    ).json()
    rep = disc["import_report"] or {"created": 0, "duplicates": []}
    print(
        f"  mock discovery (agrifood_b2b): found {disc['found']}, new {rep['created']}, "
        f"duplicates {len(rep['duplicates'])}"
    )

    header(4, "Enrich company information from their websites")
    run = call("POST", "/enrichment/run", json={"limit": 500}).json()
    print(f"processed {run['processed']}  ok {run['succeeded']}  failed {run['failed']}")
    for d in run["details"]:
        if d.get("status") not in ("success",):
            note = d.get("error") or "missing: " + ", ".join(d.get("missing") or [])
            print(f"  lead #{d['company_id']}: {d.get('status')} - {note}")

    header(5, "Score leads against their profile")
    run = call("POST", "/scoring/run", json={"limit": 500, "use_llm": args.use_llm}).json()
    print(f"scored {run['succeeded']} leads (LLM layer: {'on' if args.use_llm else 'off'})")
    best_by_profile = {}
    for profile in DEMO_PROFILES:
        leads = call("GET", "/leads", params={"profile": profile, "limit": 100}).json()["items"]
        best_by_profile[profile] = leads[0]
        print(f"\n  [{profile}]")
        print(f"  {'score':>5}  {'status':<17} {'company':<38} {'city'}")
        for lead in leads:
            print(
                f"  {lead['score'] if lead['score'] is not None else '-':>5}  "
                f"{(lead['qualification_status'] or '-'):<17} {lead['name'][:38]:<38} "
                f"{lead['city'] or lead['country'] or '?'}"
            )
    best = best_by_profile["restaurants"]
    detail = call("GET", f"/leads/{best['id']}").json()
    print(f"\n  Explanation for '{best['name']}':")
    print(f"  {detail['latest_score']['reasoning']}")
    for c in detail["latest_score"]["breakdown"]:
        reasons = "; ".join(c["reasons"])
        print(f"    {c['criterion']:<28} {c['points']:>2}/{c['max_points']:<3} {reasons}")

    header(6, "Generate outreach drafts (for human review, never sent)")
    run = call("POST", "/outreach/generate", json={"limit": 500, "use_llm": args.use_llm}).json()
    print(f"drafts created: {run['succeeded']}")
    drafts = call("GET", "/outreach/drafts", params={"company_id": best["id"]}).json()
    if drafts:
        print(f"\n  Subject: {drafts[0]['subject']}\n")
        print("  " + drafts[0]["body"].replace("\n", "\n  "))

    header(7, "Export qualified leads")
    r = call("POST", "/crm/export/csv", json={})
    print(
        f"exported {r.headers['X-Exported-Rows']} qualified leads "
        f"({r.headers['Content-Disposition'].split('filename=')[-1]} in EXPORT_DIR)"
    )

    header(8, "Pipeline analytics")
    s = call("GET", "/analytics/summary").json()
    print(
        f"total leads {s['total_leads']} | enriched {s['enriched_leads']} | "
        f"qualified {s['qualified_leads']} | review {s['needs_review_leads']} | "
        f"disqualified {s['disqualified_leads']} | insufficient {s['insufficient_data_leads']}"
    )
    print(
        f"average score {s['average_score']} | duplicates blocked {s['duplicates_blocked']} | "
        f"drafts {s['drafts_generated']} | measured API/LLM cost ${s['total_cost_usd']:.4f}"
    )
    print("by profile: " + ", ".join(f"{c['key']}={c['count']}" for c in s["by_profile"]))
    print("conversion: " + ", ".join(f"{k}={v}" for k, v in s["conversion"].items()))
    print("\nDone. Open the dashboard (streamlit run dashboard/app.py) to explore the results,")
    print("or run a real search there: type e.g. 'κρεοπωλεία' and a city.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
