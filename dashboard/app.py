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
COUNTRIES = ["GR", "CY", "IT", "ES", "PT", "FR", "DE", "NL", "BE", "AT", "BG", "RO", "PL", "GB"]

st.set_page_config(page_title="Lead Intelligence", page_icon="🎯", layout="wide")


def api(method: str, path: str, timeout: float = 180, **kwargs):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        r = httpx.request(method, f"{API_URL}{path}", headers=headers, timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        st.error(f"Cannot reach the API at {API_URL}: {exc}")
        st.stop()
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        st.error(f"{detail}")
        return None
    return r


@st.cache_data(ttl=15)
def load_summary(profile: str | None):
    r = api("GET", "/analytics/summary", params={"profile": profile} if profile else {})
    return r.json() if r else None


@st.cache_data(ttl=15)
def load_leads(params: dict):
    r = api("GET", "/leads", params={**params, "limit": 500})
    return r.json() if r else {"total": 0, "items": []}


@st.cache_data(ttl=30)
def load_profiles():
    r = api("GET", "/profiles")
    return r.json() if r else []


def refresh():
    st.cache_data.clear()
    st.rerun()


def lead_table(items: list[dict]):
    df = pd.DataFrame(items)
    cols = [
        "id",
        "score",
        "qualification_status",
        "name",
        "city",
        "country",
        "industry",
        "company_type",
        "website",
        "source",
        "profile",
    ]
    st.dataframe(
        df[[c for c in cols if c in df]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "score": st.column_config.ProgressColumn(
                "score", min_value=0, max_value=100, format="%d"
            ),
            "website": st.column_config.LinkColumn("website"),
        },
    )


st.title("🎯 AI Lead Intelligence Engine")
st.caption(
    "Describe the businesses you want, in any language. The engine finds them, reads their "
    "websites, scores them with explanations and drafts outreach for you to review. "
    "Nothing is ever sent automatically."
)

profiles = load_profiles()
profile_names = [p["name"] for p in profiles if "error" not in p]
labels = {p["name"]: p.get("label", p["name"]) for p in profiles}
with st.sidebar:
    st.header("View")
    view_profile = st.selectbox(
        "Profile / search",
        ["(all)", *profile_names],
        format_func=lambda n: "All profiles" if n == "(all)" else f"{labels.get(n, n)} · {n}",
        help="Filters the Overview, Leads and Outreach tabs.",
    )
    selected = None if view_profile == "(all)" else view_profile
    if st.button("↻ Refresh data"):
        refresh()
    st.caption(f"API: {API_URL}")

tab_search, tab_overview, tab_leads, tab_outreach, tab_ops = st.tabs(
    ["🔎 Search", "Overview", "Leads", "Outreach review", "Operations"]
)

# ----------------------------------------------------------------------------- search
with tab_search:
    st.subheader("What kind of businesses are you looking for?")
    st.caption(
        "Type it the way you would say it, in Greek or English: *κρεοπωλεία*, *dental clinics*, "
        "*καφετέριες στη Θεσσαλονίκη*, *vegan restaurants*. You will see exactly how it was "
        "understood before anything is searched."
    )
    c1, c2, c3 = st.columns([3, 2, 1])
    query = c1.text_input("Businesses", placeholder="e.g. κρεοπωλεία")
    city = c2.text_input("City (optional)", placeholder="e.g. Αθήνα")
    country = c3.selectbox("Country", COUNTRIES)
    c4, c5, c6, c7 = st.columns([1, 1, 1, 3])
    radius = c4.number_input("Radius (km)", 1.0, 50.0, 5.0, 1.0, disabled=not city)
    limit = c5.number_input("Max results", 1, 200, 20)
    with_site = c6.checkbox(
        "Only with website",
        value=True,
        help="Businesses without a website cannot be enriched and usually score low.",
    )
    offer = c7.text_input(
        "What do you sell? (optional, used in the email drafts)",
        placeholder="e.g. an online booking app for small shops",
    )
    payload = {
        "query": query,
        "country": country,
        "city": city or None,
        "radius_km": radius,
        "limit": int(limit),
        "offer": offer or None,
        "require_website": with_site,
    }

    b1, b2, _ = st.columns([1, 1, 4])
    if b1.button("1 · Preview", disabled=len(query.strip()) < 2):
        r = api("POST", "/search/interpret", json=payload)
        st.session_state["plan"] = r.json() if r else None
    plan = st.session_state.get("plan")
    if plan and plan["query"] == query:
        method_label = {
            "catalog": "built-in catalog",
            "llm": "AI (LLM)",
            "name_match": "business-name match",
        }[plan["method"]]
        with st.container(border=True):
            st.markdown(f"#### Understood as: **{plan['label']}**")
            st.write(plan["explanation"])
            m1, m2, m3 = st.columns(3)
            m1.markdown(f"**Method**  \n{method_label}")
            m2.markdown(
                "**Where**  \n"
                + (
                    f"{plan['city']} · {plan['radius_km']:g} km · "
                    if plan["city"]
                    else "whole country · "
                )
                + plan["country"]
            )
            m3.markdown("**Sources**  \n" + ", ".join(plan["sources"]))
            if plan["osm_tags"]:
                st.markdown(
                    "**OpenStreetMap categories:** " + ", ".join(f"`{t}`" for t in plan["osm_tags"])
                )
            if plan["name_keywords"]:
                st.markdown(
                    "**Name contains:** " + ", ".join(f"`{k}`" for k in plan["name_keywords"])
                )
            for w in plan["warnings"]:
                st.warning(w)
            st.caption(
                f"Results will be saved under the profile `{plan['profile_name']}` "
                "(profiles/ folder), which you can edit to tune the scoring."
            )
    if b2.button("2 · Search & score", type="primary", disabled=len(query.strip()) < 2):
        with st.spinner("Searching, reading websites and scoring… this can take a few minutes."):
            r = api("POST", "/search/run", json=payload, timeout=1800)
        if r:
            res = r.json()
            st.session_state["plan"] = res["plan"]
            st.cache_data.clear()
            found = sum(d["found"] for d in res["discovery"])
            new = sum((d["import_report"] or {}).get("created", 0) for d in res["discovery"])
            st.success(
                f"Found {found} {'business' if found == 1 else 'businesses'} ({new} new). "
                f"Showing all leads of '{res['plan']['label']}'."
            )
            for w in res["plan"]["warnings"]:
                st.warning(w)
            if res["leads"]:
                lead_table(res["leads"])
                st.caption(
                    "Open the Leads tab (pick this profile in the sidebar) to see why "
                    "each business got its score, and Outreach review for the drafts."
                )
            else:
                st.info(
                    "No businesses with a website were found. Try a bigger radius, another "
                    "city or different words."
                )

# --------------------------------------------------------------------------- overview
summary = load_summary(selected)
with tab_overview:
    if summary is None:
        st.stop()
    k = st.columns(6)
    k[0].metric("Leads", summary["total_leads"])
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
        if not selected:
            st.subheader("Leads by profile")
            st.dataframe(
                pd.DataFrame(summary["by_profile"]).rename(
                    columns={"key": "profile", "count": "leads"}
                ),
                hide_index=True,
                use_container_width=True,
            )

    c1, c2 = st.columns(2)
    for col, key, title in (
        (c1, "by_country", "Leads by country"),
        (c2, "by_industry", "Leads by industry"),
    ):
        with col:
            st.subheader(title)
            df = pd.DataFrame(summary[key])
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
        st.info("No metered calls yet. Costs appear when paid APIs or the LLM layer are used.")
    else:
        st.dataframe(costs, hide_index=True, use_container_width=True)
        st.caption(
            "Costs are computed from token / request counts and the unit prices in .env. "
            "OpenStreetMap, Wikidata and the mock LLM cost $0."
        )

# ------------------------------------------------------------------------------ leads
with tab_leads:
    f = st.columns(5)
    countries = ["(all)"] + [c["key"] for c in summary["by_country"] if c["key"] != "unknown"]
    industries = ["(all)"] + [c["key"] for c in summary["by_industry"]]
    country_f = f[0].selectbox("Country", countries)
    industry_f = f[1].selectbox("Industry", industries)
    qualification = f[2].selectbox(
        "Qualification", ["(all)", "qualified", "needs_review", "disqualified", "insufficient_data"]
    )
    min_score = f[3].slider("Min score", 0, 100, 0)
    q = f[4].text_input("Search name / domain")
    params = {
        k: v
        for k, v in {
            "profile": selected,
            "country": None if country_f == "(all)" else country_f,
            "industry": None if industry_f == "(all)" else industry_f,
            "qualification": None if qualification == "(all)" else qualification,
            "min_score": min_score or None,
            "q": q or None,
        }.items()
        if v is not None
    }
    data = load_leads(params)
    st.caption(f"{data['total']} leads")
    if data["items"]:
        lead_table(data["items"])
        options = {f"#{r['id']} {r['name']}": r["id"] for r in data["items"]}
        choice = st.selectbox("Inspect lead", list(options))
        detail = api("GET", f"/leads/{options[choice]}").json()
        score, enr = detail.get("latest_score"), detail.get("enrichment")
        a, b = st.columns([3, 2])
        with a:
            st.markdown(f"### {detail['name']}")
            st.write(
                f"{detail.get('website') or 'no website'} · "
                f"{detail.get('city') or ''} {detail.get('country') or '?'} · "
                f"{detail['industry']} · {detail['company_type']} · "
                f"profile `{detail['profile']}`"
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
            st.markdown("#### What the website says")
            if enr:
                st.write(
                    f"**Status:** {enr['status']} · website verified: "
                    f"{'yes' if enr['website_verified'] else 'no'}"
                )
                st.write(f"**Summary** ({enr['summary_method'] or '–'}): {enr['summary'] or '–'}")
                st.write(f"**Offerings:** {', '.join(enr['categories']) or '–'}")
                st.write(f"**Keywords found:** {', '.join(enr['products']) or '–'}")
                st.write(f"**Signals:** {', '.join(enr['business_signals']) or '–'}")
                if enr["negative_signals"]:
                    st.write(f"**Negative signals:** {', '.join(enr['negative_signals'])}")
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
    drafts = api("GET", "/outreach/drafts", params={"status": status_filter}) or None
    drafts = drafts.json() if drafts else []
    leads_index = {lead["id"]: lead for lead in load_leads({})["items"]}
    if selected:
        drafts = [
            d for d in drafts if leads_index.get(d["company_id"], {}).get("profile") == selected
        ]
    if not drafts:
        st.info("No drafts with this status.")
    for d in drafts:
        name = leads_index.get(d["company_id"], {}).get("name", d["company_id"])
        with st.expander(f"#{d['id']} · {name} · {d['subject']}"):
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
        import_profile = st.selectbox(
            "Profile for the imported leads",
            profile_names,
            format_func=lambda n: f"{labels.get(n, n)} · {n}",
        )
        upload = st.file_uploader("CSV with at least a 'name' column", type=["csv"])
        if upload and st.button("Import"):
            r = api(
                "POST",
                "/leads/import",
                data={"profile": import_profile},
                files={"file": (upload.name, upload.getvalue(), "text/csv")},
            )
            if r:
                rep = r.json()
                st.success(
                    f"Created {rep['created']}, duplicates {len(rep['duplicates'])}, "
                    f"invalid {len(rep['invalid'])}"
                )
                st.json(rep, expanded=False)
        st.subheader("Discover with an existing profile")
        providers = api("GET", "/discovery/providers").json()
        provider = st.selectbox(
            "Provider",
            [p["name"] for p in providers if p["configured"]],
            help="Providers that need credentials appear once configured.",
        )
        disc_profile = st.selectbox(
            "Profile",
            profile_names,
            key="disc_profile",
            format_func=lambda n: f"{labels.get(n, n)} · {n}",
        )
        dc = st.text_input("Countries (ISO codes, comma separated)", "GR")
        dcity = st.text_input("Cities (comma separated, optional)", "")
        dl = st.number_input("Limit", 1, 200, 20, key="disc_limit")
        if st.button("Search & save"):
            body = {
                "provider": provider,
                "profile": disc_profile,
                "limit": int(dl),
                "countries": [c.strip() for c in dc.split(",") if c.strip()],
                "cities": [c.strip() for c in dcity.split(",") if c.strip()],
            }
            r = api("POST", "/discovery/search", json=body, timeout=600)
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
                r = api(
                    "POST", "/pipeline/run", json={"use_llm": use_llm, "limit": 500}, timeout=1800
                )
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
