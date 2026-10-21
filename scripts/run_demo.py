"""Reproducible end-to-end demo on SYNTHETIC data, driven through the REST API.

    python scripts/run_demo.py                 # in-process API against DATABASE_URL
    python scripts/run_demo.py --api http://localhost:8000
    python scripts/run_demo.py --reset --use-llm

Steps: import CSV -> detect duplicates -> mock discovery -> enrich -> score ->
draft outreach -> export qualified leads -> pipeline analytics.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEMO_CSV = ROOT / "data" / "demo" / "synthetic_companies.csv"


def header(n: int, title: str) -> None:
    print(f"\n{'=' * 78}\n {n}. {title}\n{'=' * 78}")


def make_client(api: str | None):
    if api:
        import httpx

        headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}
        return httpx.Client(base_url=api, headers=headers, timeout=120)
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, headers={"X-API-Key": os.environ.get("API_KEY", "")})


def reset_synthetic() -> None:
    """Delete only synthetic demo leads (real leads are never touched)."""
    from sqlalchemy import delete

    from app.db.models import Company, ImportBatch
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        removed = db.execute(delete(Company).where(Company.is_synthetic.is_(True))).rowcount
        db.execute(
            delete(ImportBatch).where(
                ImportBatch.label.in_(["synthetic_companies.csv", "discovery:mock"])
            )
        )
        db.commit()
    print(f"reset: removed {removed} synthetic leads")


def ensure_schema() -> None:
    from alembic.config import Config

    from alembic import command

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

    header(1, "Import leads from CSV")
    with DEMO_CSV.open("rb") as fh:
        report = call(
            "POST", "/leads/import", files={"file": ("synthetic_companies.csv", fh, "text/csv")}
        ).json()
    print(
        f"rows: {report['total_rows']}  created: {report['created']}  "
        f"duplicates: {len(report['duplicates'])}  invalid: {len(report['invalid'])}"
    )
    for err in report["invalid"]:
        print(f"  invalid row {err['row']}: {err['error']}")

    header(2, "Duplicate detection")
    for d in report["duplicates"]:
        print(
            f"  '{d['name']}' -> existing #{d['matched_company_id']} '{d['matched_name']}' "
            f"({d['reason']}, similarity {d['similarity']})"
        )
    disc = call("POST", "/discovery/search", json={"provider": "mock", "limit": 50}).json()
    rep = disc["import_report"] or {"created": 0, "duplicates": []}
    print(
        f"mock discovery: found {disc['found']}, new {rep['created']}, "
        f"duplicates {len(rep['duplicates'])}"
    )

    header(3, "Enrich company information")
    run = call("POST", "/enrichment/run", json={"limit": 500}).json()
    print(f"processed {run['processed']}  ok {run['succeeded']}  failed {run['failed']}")
    for d in run["details"]:
        if d.get("status") not in ("success",):
            note = d.get("error") or "missing: " + ", ".join(d.get("missing") or [])
            print(f"  lead #{d['company_id']}: {d.get('status')} - {note}")

    header(4, "Calculate lead scores")
    run = call("POST", "/scoring/run", json={"limit": 500, "use_llm": args.use_llm}).json()
    print(f"scored {run['succeeded']} leads (LLM layer: {'on' if args.use_llm else 'off'})")
    leads = call("GET", "/leads", params={"limit": 100}).json()["items"]
    print(f"\n  {'score':>5}  {'status':<17} {'company':<38} {'country'}")
    for lead in leads:
        print(
            f"  {lead['score'] if lead['score'] is not None else '-':>5}  "
            f"{(lead['qualification_status'] or '-'):<17} {lead['name'][:38]:<38} "
            f"{lead['country'] or '?'}"
        )
    best = leads[0]
    detail = call("GET", f"/leads/{best['id']}").json()
    print(f"\n  Explanation for '{best['name']}':")
    print(f"  {detail['latest_score']['reasoning']}")
    for c in detail["latest_score"]["breakdown"]:
        reasons = "; ".join(c["reasons"])
        print(f"    {c['criterion']:<28} {c['points']:>2}/{c['max_points']:<3} {reasons}")

    header(5, "Generate outreach drafts (for human review, never sent)")
    run = call("POST", "/outreach/generate", json={"limit": 500, "use_llm": args.use_llm}).json()
    print(f"drafts created: {run['succeeded']}")
    drafts = call("GET", "/outreach/drafts", params={"company_id": best["id"]}).json()
    if drafts:
        print(f"\n  Subject: {drafts[0]['subject']}\n")
        print("  " + drafts[0]["body"].replace("\n", "\n  "))

    header(6, "Export qualified leads")
    r = call("POST", "/crm/export/csv", json={})
    print(
        f"exported {r.headers['X-Exported-Rows']} qualified leads "
        f"({r.headers['Content-Disposition'].split('filename=')[-1]} in EXPORT_DIR)"
    )

    header(7, "Pipeline analytics")
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
    print("conversion: " + ", ".join(f"{k}={v}" for k, v in s["conversion"].items()))
    print("\nDone. Open the dashboard (streamlit run dashboard/app.py) to explore the results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
