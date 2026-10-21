"""Internal Streamlit dashboard. Talks to the FastAPI backend only (API_URL / API_KEY)."""

from __future__ import annotations

import os

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
ACCENT = "#2f7d4f"
STATUS_COLORS = {
    "qualified": "#2f7d4f",
    "needs_review": "#d9a21b",
    "disqualified": "#b5473a",
    "insufficient_data": "#8a8f98",
}

st.set_page_config(page_title="Lead Intelligence", page_icon="🌾", layout="wide")


def api(method: str, path: str, **kwargs):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        r = httpx.request(method, f"{API_URL}{path}", headers=headers, timeout=180, **kwargs)
    except httpx.HTTPError as exc:
        st.error(f"Cannot reach the API at {API_URL}: {exc}")
        st.stop()
    if r.status_code >= 400:
        st.error(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return None
    return r


@st.cache_data(ttl=15)
def load_summary():
    r = api("GET", "/analytics/summary")
    return r.json() if r else None


@st.cache_data(ttl=15)
def load_leads(params: dict):
    r = api("GET", "/leads", params={**params, "limit": 500})
    return r.json() if r else {"total": 0, "items": []}


def refresh():
    st.cache_data.clear()
    st.rerun()


st.title("🌾 AI Lead Intelligence Engine")
st.caption(
    "Agri-food B2B lead discovery, enrichment and explainable qualification. "
    "Outreach drafts require human review and are never sent automatically."
)

summary = load_summary()
if summary is None:
    st.stop()

tab_overview, tab_leads, tab_outreach, tab_ops = st.tabs(
    ["Overview", "Leads", "Outreach review", "Operations"]
)

# --------------------------------------------------------------------------- overview
with tab_overview:
    k = st.columns(6)
    k[0].metric("Discovered leads", summary["total_leads"])
    k[1].metric("Enriched", summary["enriched_leads"])
    k[2].metric("Qualified", summary["qualified_leads"])
    k[3].metric(
        "Avg. score", summary["average_score"] if summary["average_score"] is not None else "–"
    )
    k[4].metric("Duplicates blocked", summary["duplicates_blocked"])
    k[5].metric("Measured API/LLM cost", f"${summary['total_cost_usd']:.4f}")

    left, right = st.columns(2)
    with left:
        st.subheader("Pipeline funnel")
        funnel = pd.DataFrame(
            {
                "stage": [
                    "Discovered",
                    "Enriched",
                    "Scored",
                    "Qualified",
                    "Drafted",
                    "Approved",
                    "Synced to CRM",
                ],
                "leads": [
                    summary["total_leads"],
                    summary["enriched_leads"],
                    summary["scored_leads"],
                    summary["qualified_leads"],
                    summary["drafts_generated"],
                    summary["drafts_approved"],
                    summary["crm_synced"],
                ],
            }
        )
        fig = px.funnel(funnel, x="leads", y="stage", color_discrete_sequence=[ACCENT])
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=330)
        st.plotly_chart(fig, use_container_width=True)
        conv = pd.DataFrame(
            [
                {
                    "step": k.replace("_to_", " → ").replace("_", " "),
                    "conversion": f"{v:.0%}" if v is not None else "–",
                }
                for k, v in summary["conversion"].items()
            ]
        )
        st.table(conv.set_index("step"))
    with right:
        st.subheader("Score distribution")
        dist = pd.DataFrame(summary["score_distribution"])
        fig = px.bar(
            dist,
            x="key",
            y="count",
            labels={"key": "score", "count": "leads"},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=330)
        st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Leads by country")
        df = pd.DataFrame(summary["by_country"])
        if not df.empty:
            fig = px.bar(
                df.sort_values("count"),
                x="count",
                y="key",
                orientation="h",
                labels={"key": "", "count": "leads"},
                color_discrete_sequence=[ACCENT],
            )
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380)
            st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.subheader("Leads by industry")
        df = pd.DataFrame(summary["by_industry"])
        if not df.empty:
            fig = px.bar(
                df.sort_values("count"),
                x="count",
                y="key",
                orientation="h",
                labels={"key": "", "count": "leads"},
                color_discrete_sequence=[ACCENT],
            )
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380)
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("API / LLM usage")
    costs = pd.DataFrame(summary["costs"])
    if costs.empty:
        st.info("No metered calls yet. Costs appear when discovery APIs or the LLM layer are used.")
    else:
        st.dataframe(costs, hide_index=True, use_container_width=True)
        st.caption(
            "Costs are computed from token counts / request counts and the unit prices "
            "configured in .env. The mock LLM always costs $0."
        )

# ------------------------------------------------------------------------------ leads
with tab_leads:
    f = st.columns(5)
    countries = ["(all)"] + [c["key"] for c in summary["by_country"] if c["key"] != "unknown"]
    industries = ["(all)"] + [c["key"] for c in summary["by_industry"]]
    country = f[0].selectbox("Country", countries)
    industry = f[1].selectbox("Industry", industries)
    qualification = f[2].selectbox(
        "Qualification", ["(all)", "qualified", "needs_review", "disqualified", "insufficient_data"]
    )
    min_score = f[3].slider("Min score", 0, 100, 0)
    query = f[4].text_input("Search name / domain")
    params = {
        k: v
        for k, v in {
            "country": None if country == "(all)" else country,
            "industry": None if industry == "(all)" else industry,
            "qualification": None if qualification == "(all)" else qualification,
            "min_score": min_score or None,
            "q": query or None,
        }.items()
        if v is not None
    }
    data = load_leads(params)
    st.caption(f"{data['total']} leads")
    if data["items"]:
        df = pd.DataFrame(data["items"])
        view = df[
            [
                "id",
                "score",
                "qualification_status",
                "name",
                "country",
                "industry",
                "company_type",
                "status",
                "domain",
                "source",
                "is_synthetic",
            ]
        ]
        st.dataframe(
            view,
            hide_index=True,
            use_container_width=True,
            column_config={
                "score": st.column_config.ProgressColumn(
                    "score", min_value=0, max_value=100, format="%d"
                )
            },
        )
        options = {f"#{r['id']} {r['name']}": r["id"] for r in data["items"]}
        choice = st.selectbox("Inspect lead", list(options))
        detail = api("GET", f"/leads/{options[choice]}").json()
        score, enr = detail.get("latest_score"), detail.get("enrichment")
        a, b = st.columns([3, 2])
        with a:
            st.markdown(f"### {detail['name']}")
            st.write(
                f"{detail.get('website') or 'no website'} · {detail.get('country') or '?'} · "
                f"{detail['industry']} · {detail['company_type']}"
            )
            if detail["is_synthetic"]:
                st.caption("Synthetic demo company")
            if score:
                color = STATUS_COLORS.get(score["status"], "#666")
                st.markdown(
                    f"<span style='background:{color};color:white;padding:2px 10px;"
                    f"border-radius:10px'>{score['status']}</span> "
                    f"**{score['score']}/100** · confidence {score['confidence']:.0%} · "
                    f"rules {score['rule_score']} {score['llm_adjustment']:+d} LLM",
                    unsafe_allow_html=True,
                )
                st.write(score["reasoning"])
                bd = pd.DataFrame(score["breakdown"])
                bd["reasons"] = bd["reasons"].apply("; ".join)
                st.dataframe(bd, hide_index=True, use_container_width=True)
                if score.get("llm_analysis"):
                    with st.expander("LLM analysis"):
                        st.json(score["llm_analysis"])
            else:
                st.info("Not scored yet.")
        with b:
            st.markdown("#### Enrichment")
            if enr:
                st.write(
                    f"**Status:** {enr['status']} · website verified: "
                    f"{'yes' if enr['website_verified'] else 'no'}"
                )
                st.write(f"**Summary** ({enr['summary_method'] or '–'}): {enr['summary'] or '–'}")
                st.write(f"**Categories:** {', '.join(enr['categories']) or '–'}")
                st.write(f"**Products:** {', '.join(enr['products']) or '–'}")
                st.write(f"**B2B signals:** {', '.join(enr['business_signals']) or '–'}")
                st.write(
                    f"**Location:** {enr['location'] or '–'} "
                    f"({enr['country_evidence'] or 'no country evidence'})"
                )
                if enr["missing_fields"]:
                    st.warning("Missing: " + ", ".join(enr["missing_fields"]))
                if enr["error"]:
                    st.caption(f"Note: {enr['error']}")
                st.caption(
                    "Sources: "
                    + ", ".join(enr["source_urls"])
                    + f" · enriched {enr['enriched_at'][:19]}"
                )
            else:
                st.info("Not enriched yet.")

# --------------------------------------------------------------------------- outreach
with tab_outreach:
    status_filter = st.radio("Show", ["pending_review", "approved", "rejected"], horizontal=True)
    drafts = api("GET", "/outreach/drafts", params={"status": status_filter}).json()
    if not drafts:
        st.info("No drafts with this status.")
    names = {lead["id"]: lead["name"] for lead in load_leads({})["items"]}
    for d in drafts:
        with st.expander(
            f"#{d['id']} · {names.get(d['company_id'], d['company_id'])} · {d['subject']}"
        ):
            st.caption(f"method: {d['generation_method']} · created {d['created_at'][:19]}")
            for w in d["warnings"]:
                st.warning(w)
            subject = st.text_input("Subject", d["subject"], key=f"s{d['id']}")
            body = st.text_area("Body", d["body"], height=260, key=f"b{d['id']}")
            notes = st.text_input(
                "Reviewer notes", d.get("reviewer_notes") or "", key=f"n{d['id']}"
            )
            c1, c2, _ = st.columns([1, 1, 4])
            payload = {"subject": subject, "body": body, "reviewer_notes": notes or None}
            if c1.button("Approve", key=f"a{d['id']}", type="primary"):
                api("PATCH", f"/outreach/drafts/{d['id']}", json={**payload, "status": "approved"})
                refresh()
            if c2.button("Reject", key=f"r{d['id']}"):
                api("PATCH", f"/outreach/drafts/{d['id']}", json={**payload, "status": "rejected"})
                refresh()
            with st.popover("Verified facts used"):
                st.json(d["facts_used"])

# ------------------------------------------------------------------------- operations
with tab_ops:
    o1, o2 = st.columns(2)
    with o1:
        st.subheader("Import CSV")
        upload = st.file_uploader("CSV with at least a 'name' column", type=["csv"])
        if upload and st.button("Import"):
            r = api(
                "POST",
                "/leads/import",
                files={"file": (upload.name, upload.getvalue(), "text/csv")},
            )
            if r:
                rep = r.json()
                st.success(
                    f"Created {rep['created']}, duplicates {len(rep['duplicates'])}, "
                    f"invalid {len(rep['invalid'])}"
                )
                st.json(rep, expanded=False)
        st.subheader("Discover leads")
        providers = api("GET", "/discovery/providers").json()
        names_ok = [p["name"] for p in providers if p["configured"]]
        provider = st.selectbox(
            "Provider", names_ok, help="Providers that need credentials appear once configured."
        )
        dc = st.text_input("Countries (ISO codes, comma separated)", "GR,IT,ES")
        di = st.multiselect(
            "Industries",
            [
                "agriculture",
                "food_wholesale",
                "import_export",
                "food_distribution",
                "food_manufacturing",
            ],
        )
        dk = st.text_input("Keywords (comma separated)", "")
        dl = st.number_input("Limit", 1, 200, 20)
        if st.button("Search & save"):
            body = {
                "provider": provider,
                "limit": int(dl),
                "countries": [c.strip() for c in dc.split(",") if c.strip()],
                "industries": di,
                "keywords": [k.strip() for k in dk.split(",") if k.strip()],
            }
            r = api("POST", "/discovery/search", json=body)
            if r:
                res = r.json()
                rep = res["import_report"] or {"created": 0, "duplicates": []}
                st.success(
                    f"Found {res['found']}, new {rep['created']}, "
                    f"duplicates {len(rep['duplicates'])}"
                )
    with o2:
        st.subheader("Run pipeline")
        use_llm = st.toggle(
            "Use LLM layer",
            value=False,
            help="Uses LLM_PROVIDER from the API's environment (mock by default).",
        )
        if st.button("Enrich → score → draft new leads", type="primary"):
            with st.spinner("Running pipeline…"):
                r = api("POST", "/pipeline/run", json={"use_llm": use_llm, "limit": 500})
            if r:
                st.json(r.json(), expanded=False)
                st.cache_data.clear()
        st.subheader("Export")
        incl = st.checkbox("Include needs_review leads")
        if st.button("Prepare CSV export"):
            r = api("POST", "/crm/export/csv", json={"include_needs_review": incl})
            if r:
                st.download_button(
                    "Download CSV", r.content, file_name="qualified_leads.csv", mime="text/csv"
                )
        st.subheader("CRM sync failures")
        failures = api("GET", "/crm/sync-records", params={"status": "failed"}).json()
        if failures:
            st.dataframe(pd.DataFrame(failures), hide_index=True, use_container_width=True)
        else:
            st.caption("No failed syncs.")
    if st.button("↻ Refresh data"):
        refresh()
