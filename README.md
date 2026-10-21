# AI Lead Intelligence Engine

[![CI](https://github.com/Adreas-AI/ai-lead-intelligence-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Adreas-AI/ai-lead-intelligence-engine/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Type the kind of business you want, in plain words and in any language.** For example *"κρεοπωλεία στην Αθήνα"*, *"dental clinics in Thessaloniki"* or *"vegan restaurants"*.

The engine does the rest:

1. It shows you exactly how it understood the request.
2. It finds matching businesses.
3. It reads their own websites.
4. It scores each one against your Ideal Customer Profile, with an explanation for every point.
5. It drafts a personalised email for you to review.

It works for any vertical: butchers, salons, clinics, restaurants, accountants, wholesalers and so on.

> Outreach is never sent automatically. Every draft waits for human review.
> Missing company information is never invented. Gaps are recorded as `missing_fields`.

---

## Contents

- [What it does](#what-it-does)
- [How a search is understood](#how-a-search-is-understood)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Installation](#installation)
- [Environment configuration](#environment-configuration)
- [Demo](#demo)
- [Running a real search](#running-a-real-search)
- [Ideal Customer Profiles](#ideal-customer-profiles)
- [Example API requests](#example-api-requests)
- [Screenshots](#screenshots)
- [How qualification works](#how-qualification-works)
- [Testing](#testing)
- [Business Impact & GTM Use Cases](#business-impact--gtm-use-cases)
- [Limitations and future improvements](#limitations-and-future-improvements)

---

## What it does

**The problem.** Prospecting for any B2B offer starts the same way: someone searches maps and directories, opens websites one by one, copies details into a spreadsheet and decides by gut feeling who is worth contacting. This is slow and inconsistent. Duplicates pile up in the CRM, and outreach is either generic or expensive to personalise.

**The solution.** One repeatable pipeline that is the same for every vertical:

| Stage | What happens | Output |
|---|---|---|
| **Search** | Free-text query → a visible search plan (categories, map tags, location, sources) | A saved, editable profile |
| **Discovery** | OpenStreetMap (free), Google Places (optional), Wikidata, CSV import | Leads with source URLs |
| **Deduplication** | Same domain, same legal name + country, or fuzzy name (Greek and Latin scripts) | Clean list, import report |
| **Enrichment** | Fetch the official site (robots.txt respected), verify it, extract offerings, signals, location | Evidence with sources and timestamps |
| **Qualification** | Deterministic 0–100 score from the profile's weights, hard gates, optional LLM review (±10) | JSON: score, reasoning, confidence, status |
| **Outreach** | Short email from verified facts plus *your* pitch; guardrails block invented claims | Drafts waiting for review |
| **CRM** | CSV export or HubSpot upsert without duplicates; failures logged | Synced records |
| **Analytics** | Funnel, conversion, breakdowns per profile, country and industry, measured API/LLM cost | Dashboard and API |

---

## How a search is understood

Before anything is searched, `POST /search/interpret` (or the **Preview** button in the dashboard) shows the plan:

```
You typed:   "κρεοπωλεία στην Αθήνα"
Method:      catalog
Understood:  Matched the built-in business catalog: Butcher shops (Κρεοπωλεία).
             Searching: Αθήνα (within 5 km), Greece. Sources: openstreetmap.
OSM tags:    shop=butcher
```

There are three ways to interpret a query, and the plan always says which one was used:

| Method | When | How |
|---|---|---|
| `llm` | `LLM_PROVIDER=openai` with a key | The LLM turns *any* wording into categories, OpenStreetMap tags, local search terms and website keywords. Its output is validated: unknown or non-business tags are dropped. |
| `catalog` | Default, no key needed | A built-in bilingual (Greek/English) dictionary of about 80 business types, tolerant of accents and Greek inflection (`κρεοπωλεία`, `Κρεοπωλείο`, `butcher shops` → `shop=butcher`). |
| `name_match` | The words are not in the catalog and there is no LLM | Searches for businesses whose **name** contains the words. Clearly labelled as less complete, with a hint to add an API key. |

If you type a place ("… στην Αθήνα", "… in Patras"), it is recognised. Otherwise you can set a city and a radius.

---

## Architecture

```mermaid
flowchart LR
    U([Free-text query<br/>any language]) --> INT

    subgraph API["FastAPI service"]
        INT[Search interpreter<br/>catalog / LLM / name match]
        PROF[(ICP profiles<br/>profiles/*.yaml)]
        ING[Ingestion and<br/>duplicate detection]
        ENR[Enrichment<br/>robots.txt-aware fetcher]
        SCO[Scoring engine<br/>profile weights + gates]
        OUT[Outreach drafts<br/>guardrails]
        CRM[CRM layer<br/>CSV / HubSpot]
        ANA[Analytics]
    end

    subgraph Sources
        OSM[OpenStreetMap<br/>Overpass + Nominatim<br/>free]
        GP[Google Places<br/>optional, paid]
        WD[Wikidata<br/>free]
        CSV[CSV upload]
    end

    LLM[(LLM<br/>OpenAI / mock)]
    DB[(PostgreSQL)]
    WEB[(Company websites)]
    DASH[Streamlit dashboard]
    N8N[n8n workflows]

    INT --> PROF
    INT -. optional .-> LLM
    PROF --> OSM & GP & WD
    OSM & GP & WD & CSV --> ING --> DB
    DB --> ENR <--> WEB
    ENR --> DB --> SCO --> DB
    SCO -. optional review .-> LLM
    DB --> OUT --> DB
    OUT -. optional .-> LLM
    DB --> CRM
    DB --> ANA
    DASH --> API
    N8N --> API
```

Every outbound call goes through one HTTP client. It identifies itself, rate-limits per host with a token bucket, and retries transient failures with exponential backoff and jitter, honouring `Retry-After`. Busy OpenStreetMap servers are retried patiently, then the next public Overpass instance is tried.

```
app/
├── core/          config, structured logging, retry, rate limiting, HTTP client
├── db/            SQLAlchemy models and session
├── domain/        profiles (ICP schema + loader), business catalog, geo, keywords, normalisation
├── providers/     discovery (openstreetmap, google_places, wikidata, mock), web fetching, LLM, CRM
├── services/      search, leads/dedup, extraction, enrichment, scoring, outreach, crm, analytics
├── api/           FastAPI routes
└── main.py        app factory and middleware (request IDs, rate limiting, access logs)
profiles/          Ideal Customer Profiles (YAML); searches add their own here
alembic/           database migrations
dashboard/         Streamlit dashboard
data/demo/         SYNTHETIC demo datasets (CSV per profile, website fixtures)
n8n/               importable n8n workflow
scripts/           demo runner and demo-data generator
tests/             unit and integration tests (no network, no keys)
```

---

## Technology stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| API | FastAPI, Pydantic v2, pydantic-settings |
| Data | PostgreSQL 16, SQLAlchemy 2.0, Alembic |
| Discovery | OpenStreetMap (Overpass, Nominatim), Google Places API, Wikidata SPARQL |
| HTTP / parsing | httpx, BeautifulSoup |
| AI | OpenAI API (JSON mode), deterministic mock provider |
| Configuration | YAML profiles validated by Pydantic |
| Orchestration | n8n (optional) |
| Dashboard | Streamlit, Plotly, pandas |
| Quality | pytest, respx, ruff, GitHub Actions |
| Runtime | Docker, Docker Compose |

---

## Installation

### Option A: Docker Compose (recommended)

```bash
git clone https://github.com/Adreas-AI/ai-lead-intelligence-engine.git
cd ai-lead-intelligence-engine
cp .env.example .env            # optional: works without any keys
docker compose up -d --build
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:8501 |
| API and interactive docs | http://localhost:8000/docs |
| n8n (optional) | `docker compose --profile n8n up -d`, then http://localhost:5678 |

If a port is already used on your machine, set `DB_PORT`, `API_PORT`, `DASHBOARD_PORT` or `N8N_PORT` in `.env`. Migrations run automatically when the API starts, and `profiles/` is mounted so that profiles created by searches survive restarts.

### Option B: Local Python

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,dashboard]"
cp .env.example .env

docker compose up -d db          # or point DATABASE_URL at any PostgreSQL
alembic upgrade head
uvicorn app.main:app --reload    # API on :8000
streamlit run dashboard/app.py   # dashboard on :8501
```

For a quick experiment without PostgreSQL, set `DATABASE_URL=sqlite:///./leads.db`.

---

## Environment configuration

All settings live in `.env` (see [`.env.example`](.env.example)). Secrets are typed as `SecretStr`, so they never appear in logs. A blank value means "not configured". `.env` is git-ignored.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local PostgreSQL | SQLAlchemy URL |
| `DEFAULT_PROFILE` | `restaurants` | Profile for leads imported without one |
| `API_KEY` | empty | If set, every request needs the `X-API-Key` header |
| `LLM_PROVIDER` | `mock` | `none`, `mock` or `openai` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | empty, `gpt-4o-mini` | Smart search interpretation and LLM scoring/drafting |
| `OPENAI_INPUT_COST_PER_1M`, `OPENAI_OUTPUT_COST_PER_1M` | 0.15 / 0.60 | Unit prices for cost reporting; check current pricing |
| `GOOGLE_PLACES_API_KEY` | empty | Adds Google Places as a search source |
| `OVERPASS_URLS` | 3 public instances | OpenStreetMap servers, tried in order |
| `HUBSPOT_ACCESS_TOKEN` | empty | Enables HubSpot sync (private-app token) |
| `ENRICHMENT_MODE` | `auto` | `offline` never touches the network |
| `OUTBOUND_REQUESTS_PER_SECOND` | 1.0 | Per-host politeness limit |
| `SENDER_NAME`, `SENDER_COMPANY`, `SENDER_VALUE_PROPOSITION` | placeholders | Signature and fallback pitch in drafts |

### Which integrations need credentials

| Integration | Needs credentials? | Without credentials |
|---|---|---|
| Free-text search (catalog / name match), dedup, scoring, outreach templates, CSV export, analytics | No | Fully functional |
| **OpenStreetMap discovery** (local businesses of any kind) | **No** | Fully functional; data © OpenStreetMap contributors, ODbL |
| **Wikidata discovery** (notable companies) | **No** | Fully functional |
| **Website enrichment** | **No** | Real websites fetched politely |
| OpenAI (understands *any* query; LLM score review and drafts) | `OPENAI_API_KEY` + `LLM_PROVIDER=openai` | Built-in catalog, rule-based scoring and template drafts |
| Google Places (wider coverage, small businesses) | `GOOGLE_PLACES_API_KEY` | OpenStreetMap only |
| HubSpot sync | `HUBSPOT_ACCESS_TOKEN` | `/crm/sync` returns 503; CSV export still works |

---

## Demo

The demo uses **clearly labelled synthetic data** and needs no network. Every company is fictional and uses the reserved `.example` domain (RFC 2606), served from local HTML fixtures. There is one dataset per example profile: restaurants, hair salons and agri-food B2B. The datasets deliberately include strong and weak leads, a permanently closed business, a software vendor posing as a lead, a website that belongs to someone else, an unreachable site, duplicates written differently, and invalid rows.

```bash
docker compose exec api python scripts/run_demo.py --use-llm      # with Docker
python scripts/run_demo.py --use-llm                              # locally (--reset clears demo leads)
```

The demo walks through these steps:

1. **Understand free-text searches.** It previews `κρεοπωλεία στην Αθήνα`, `dental clinics` (Thessaloniki) and `αγροτικά μηχανήματα`, showing the catalog match, the tags and the name-match fallback.
2. **Import leads** from three CSVs, one per profile.
3. **Detect duplicates**: same domain, same name in capitals or without accents, and near-identical names.
4. **Enrich** every company from its website.
5. **Score** against each lead's own profile, with the full explanation of the best lead.
6. **Generate outreach drafts** that use each profile's pitch.
7. **Export** qualified leads to CSV.
8. **Show analytics**, including a breakdown per profile.

---

## Running a real search

**In the dashboard** (http://localhost:8501, **🔎 Search** tab):

1. Type the businesses, e.g. `κρεοπωλεία`.
2. Optionally add a city, a radius and what you sell (used in the drafts).
3. Click **Preview** to see exactly how the query was understood.
4. Click **Search & score**. Results arrive in about a minute, depending on how many websites have to be read.

**Through the API:**

```bash
curl -X POST localhost:8000/search/interpret -H 'Content-Type: application/json; charset=utf-8' \
  -d '{"query": "κρεοπωλεία στην Αθήνα"}'

curl -X POST localhost:8000/search/run -H 'Content-Type: application/json; charset=utf-8' \
  -d '{"query": "κρεοπωλεία στην Αθήνα", "limit": 20,
       "offer": "an online ordering and delivery app for local food shops"}'
```

Each search saves a profile such as `profiles/search_butcher_shops_athina.yaml`. You can edit it (weights, keywords, pitch) and re-score with `POST /scoring/run {"force": true}`.

---

## Ideal Customer Profiles

A profile describes **who you are looking for and how to judge them**. Bundled examples:

| Profile | Target | Seller pitch (example) |
|---|---|---|
| [`restaurants`](profiles/restaurants.yaml) | Independent restaurants, cafés and bars in Athens, Piraeus and Thessaloniki | Reservations and online-ordering platform |
| [`hair_salons`](profiles/hair_salons.yaml) | Hair salons and barbershops | Salon booking and client-management app |
| [`agrifood_b2b`](profiles/agrifood_b2b.yaml) | Agri-food producers, exporters, wholesalers in the EU | B2B agricultural marketplace |

Writing your own is a YAML file in `profiles/`. The structure is validated on load, so a wrong weight or tag gives a clear error.

```yaml
name: dental_clinics            # must match the file name
label: Dental clinics
description: Private dental clinics that book appointments online.
weights: {industry: 25, business_type: 15, geography: 20, business_signals: 20, offering: 10, completeness: 10}  # sum = 100
thresholds: {qualified: 65, review: 45}
gates: {require_verified_website: true, require_target_market: true}
geography: {primary_countries: [GR], cities: [Αθήνα, Athens]}
industries:
  dentist:
    label: Dental clinic
    fit: 1.0                                  # share of the industry weight
    synonyms: [dentist, dental clinic, οδοντιατρείο]
    osm_tags: ["amenity=dentist", "healthcare=dentist"]
business_types:
  independent: {label: Independent practice, fit: 1.0, synonyms: [our practice, family dentist]}
  chain: {label: Dental chain, fit: 0.5, synonyms: [our clinics, locations]}
signals:
  positive: {online_booking: [book online, appointment, ραντεβού]}
  negative: [under construction]
  disqualify: [permanently closed]           # always ruled out
offering_categories:
  - {slug: implants, label: Implants, keywords: [implants, εμφυτεύματα]}
  - {slug: orthodontics, label: Orthodontics, core: false, keywords: [braces, invisalign]}
outreach:
  value_proposition: an online booking and reminder system for dental practices
  subject: "Fewer no-shows at {name}"
  relevance_default: Practices like {name} use it to fill cancelled slots automatically.
  signal_lines: {online_booking: " I saw you already take appointments online."}
```

Then use it: `POST /discovery/search {"provider": "openstreetmap", "profile": "dental_clinics", "countries": ["GR"], "cities": ["Αθήνα"]}`, or import a CSV with `profile=dental_clinics`.

---

## Example API requests

The full OpenAPI documentation is at `/docs`. If `API_KEY` is set, add `-H "X-API-Key: $API_KEY"`.

```bash
# Profiles
curl localhost:8000/profiles
curl localhost:8000/profiles/hair_salons

# Discovery with a profile (openstreetmap | google_places | wikidata | mock)
curl -X POST localhost:8000/discovery/search -H 'Content-Type: application/json' \
  -d '{"provider":"openstreetmap","profile":"hair_salons","countries":["GR"],"cities":["Thessaloniki"],"limit":20}'

# Import a CSV for a profile (needs at least a "name" column)
curl -F "profile=restaurants" -F "file=@data/demo/restaurants.csv" localhost:8000/leads/import

# Enrich, score (optional LLM review) and draft: per stage or all at once
curl -X POST localhost:8000/pipeline/run -H 'Content-Type: application/json' -d '{"use_llm":false}'
curl -X POST "localhost:8000/leads/1/score?use_llm=true"

# Filter leads
curl "localhost:8000/leads?profile=restaurants&qualification=qualified&min_score=70"

# Review a draft, export, sync, analytics
curl -X PATCH localhost:8000/outreach/drafts/1 -H 'Content-Type: application/json' \
  -d '{"status":"approved","reviewer_notes":"OK to send manually"}'
curl -X POST localhost:8000/crm/export/csv -H 'Content-Type: application/json' -d '{}' -o qualified.csv
curl -X POST localhost:8000/crm/sync -H 'Content-Type: application/json' -d '{"provider":"hubspot"}'
curl "localhost:8000/analytics/summary?profile=restaurants"
```

Example scoring response (`POST /leads/{id}/score?use_llm=true`, synthetic demo lead, mock LLM), abridged:

```json
{
  "profile": "restaurants",
  "score": 100,
  "rule_score": 98,
  "llm_adjustment": 3,
  "confidence": 1.0,
  "status": "qualified",
  "reasoning": "Final score 100/100 (qualified). Rule-based score 98/100 (qualified) for profile 'Restaurants, cafés & bars'. Strengths: industry: Restaurant / taverna; business type: Independent / family-run. LLM review (+3): ...",
  "breakdown": [
    {"criterion": "industry_relevance", "points": 25, "max_points": 25, "reasons": ["industry: Restaurant / taverna"]},
    {"criterion": "business_type_fit",  "points": 15, "max_points": 15, "reasons": ["business type: Independent / family-run"]},
    {"criterion": "geographic_fit",     "points": 20, "max_points": 20, "reasons": ["Piraeus is a target city"]},
    {"criterion": "business_signals",   "points": 20, "max_points": 20, "reasons": ["'Independent / family-run' matches the target business model", "signals: reservations, events, menu_online, opening_hours"]},
    {"criterion": "offering_relevance", "points": 8,  "max_points": 10, "reasons": ["core offerings: Greek cuisine, Seafood"]},
    {"criterion": "data_completeness",  "points": 10, "max_points": 10, "reasons": ["available: website, website verified, summary, country, offerings detected, source URL"]}
  ],
  "scoring_version": "rules-v2+llm"
}
```

---

## Screenshots

| Search: what was understood | Search: results |
|---|---|
| ![Search preview](docs/screenshots/search_preview.png) | ![Search results](docs/screenshots/search_results.png) |

| Lead explanation | Overview | Outreach review |
|---|---|---|
| ![Lead detail](docs/screenshots/lead_detail.png) | ![Overview](docs/screenshots/overview.png) | ![Outreach review](docs/screenshots/outreach.png) |

The search screenshots show a real search for hair salons in Athens (OpenStreetMap). The other screenshots show the synthetic demo data plus those results.

---

## How qualification works

**Layer 1: deterministic rules** (always on, same input gives the same output). The six criteria and their weights come from the lead's profile:

| Criterion | Default max | Based on |
|---|---|---|
| Industry relevance | 25 | The profile's industries and their `fit`; offering evidence partly substitutes for an unknown industry |
| Business type fit | 20 | The profile's business types (e.g. independent vs franchise vs supplier) |
| Geographic fit | 15 | Primary / secondary countries and optional focus cities |
| Business signals | 15 | Matching the target model plus website signals (online booking, wholesale, delivery, …); negative signals lower it |
| Offering relevance | 15 | Core vs adjacent offering categories found on the website |
| Data completeness | 10 | Website, verification, summary, country, offerings, source URL |

**Statuses.** A score at or above the profile's `qualified` threshold is `qualified`; at or above `review` it is `needs_review`; below that it is `disqualified`. A lead with too little evidence is `insufficient_data`.

**Hard rules.**
- No verified official website, or outside the target markets: the lead is held at `needs_review` for a person to decide.
- A `disqualify` phrase on the website (e.g. "permanently closed"): the lead is always `disqualified`, and the reasoning says why.

**Confidence** reflects how much verified evidence the score rests on, not how high the score is.

**Layer 2: optional LLM review.** The LLM gets the profile, the verified facts and the rule breakdown. It returns an `adjustment` (clamped to ±10), reasoning, risks and a next step. If the LLM fails, the rule score stands and the failure is recorded.

**Outreach guardrails.** LLM drafts are rejected and replaced by the template if they leave out the company name, contain placeholders, include numbers that aren't in the facts or your pitch, use hype or guarantee language, or claim certifications that weren't verified. Facts from a website that wasn't verified are excluded from personalisation.

**Responsible data collection.** The engine respects robots.txt (RFC 9309), identifies itself, rate-limits per host, and never bypasses anti-bot measures. A 401, 403 or 429 response, or a CAPTCHA page, is recorded as `blocked`. Social profiles, webmail addresses and marketplace pages are never treated as official websites. Only company-level information is collected, never personal data about individuals.

---

## Testing

```bash
pytest                                   # 154 tests, no network, no API keys (SQLite in-memory)
pytest --cov=app --cov-report=term-missing
DATABASE_URL=postgresql+psycopg://leads:leads@localhost:5432/leads pytest   # against PostgreSQL
ruff check . && ruff format --check .
```

- **Unit tests:**
  - Greek and Latin name normalisation
  - Profile validation and every bundled profile
  - The bilingual catalog (accents, inflection, overlaps like *dental clinic* vs *clinic*, *barber* vs *bar*)
  - Search interpretation by catalog, name match and LLM, including validation and fallback
  - OpenStreetMap queries, geocoding, deduplication and server failover
  - Extraction, scoring rules, gates and disqualifiers
  - LLM clamping and fallback, outreach guardrails
  - Retry and rate limiting
  - Every external provider, with mocked HTTP
- **Integration tests:**
  - The full API flow, with a free-text search end to end (mocked OpenStreetMap plus fixture websites)
  - Per-profile scoring
  - Idempotent re-runs, CRM duplicate prevention and failure logging, auth and rate limiting
- **CI** (GitHub Actions): lint, tests on SQLite and PostgreSQL, `alembic upgrade/check/downgrade`, the end-to-end demo, and a Docker build.

---

## Business Impact & GTM Use Cases

This section explains what the system is designed to change and how to measure whether it does. **No performance or ROI figures are claimed.** The demo runs on synthetic data, so its numbers say nothing about real-world results.

### What it is designed to improve

**Less manual prospect research.** Finding businesses of a given type in a given area, opening their websites and noting what they offer and whether they fit is the repetitive part of prospecting. The engine does it from public sources and the companies' own websites, and keeps the source URLs, so a person checks evidence instead of gathering it.

**Standardised qualification.** Each profile applies the same explicit rules to every lead, and every point is explained. Teams can discuss *weights and keywords* in a versioned YAML file instead of individual opinions. Hard gates keep uncertain leads with a person.

**Fast expansion into new segments and markets.** A new vertical or city is a sentence in the search box or a new profile file, not a new research process. The same pipeline serves very different targets, such as restaurants, salons, clinics or B2B suppliers.

**Cleaner CRM.** Domain and name-based deduplication on import (Greek and Latin names), plus domain lookup and stored external IDs on sync, are designed to prevent duplicate CRM records.

### GTM use cases

1. **New-market entry.** Search and qualify a vertical in a new city or country before a launch.
2. **New segment testing.** Compare how many good-fit businesses exist for several candidate segments.
3. **Territory planning.** Split qualified leads by city or area for field or inside sales.
4. **List triage.** Score trade-fair, partner or purchased lists (CSV) in one batch and route `needs_review` leads to people.
5. **CRM hygiene.** Re-enrich existing accounts, flag near-duplicates (`GET /leads/duplicates`) and fill missing fields with evidence.
6. **SDR preparation.** Give SDRs a reviewed draft and the verified facts behind it.
7. **Scheduled prospecting.** The included n8n workflow runs discovery and qualification on a schedule and reminds the team about drafts waiting for review.

### How to measure actual results

| Question | Metric | How to measure |
|---|---|---|
| Does it save research time? | Minutes per researched lead | Time a baseline sample done manually and the same number done with the engine (review time included). Compare medians. |
| Is discovery complete enough? | Recall against a known list | For one city and vertical, compare results with a manually built list; note what is missing and why (no website, not on OSM). |
| Is enrichment accurate? | Precision per field | Have a reviewer check a random sample (e.g. 50 leads) for website, location, offerings and type; record the % correct. |
| Is scoring useful? | Agreement with experts; conversion by score band | Ask two or three people to label a sample and compute agreement. Then track reply and meeting rates per score band in the CRM. |
| Does outreach perform? | Reply rate, positive reply rate, edit rate | Track approved drafts in the CRM; record how much reviewers edit them. |
| Is the CRM cleaner? | Duplicate rate | Duplicates per 1,000 records before and after; compare with `duplicates_blocked`. |
| What does it cost? | $ per qualified lead | `total_cost_usd` from `/analytics/summary` ÷ qualified leads. |

---

## Limitations and future improvements

**Current limitations**

- **Coverage depends on the source.** OpenStreetMap is free and good for shops and local businesses, but completeness varies by city and many small businesses have no website tag. Google Places gives wider coverage but needs a paid key.
- **The catalog is bilingual.** Without an LLM, queries are understood in Greek and English, and other wording falls back to name matching. With `LLM_PROVIDER=openai`, any wording and language works.
- **Shallow, English-leaning website reading.** Only the homepage, an English alternate and one about/products page are read, which keeps the crawler polite. Profiles can include keywords in several languages; the bundled ones mix Greek and English.
- **Public OpenStreetMap servers can be slow or busy.** The engine retries and fails over between instances, but searches can take a minute or two. For heavy use, run your own Overpass instance (`OVERPASS_URLS`).
- **Synchronous runs.** A search enriches its new leads inside the request. That is fine for tens of leads; larger volumes need a job queue.
- **Licences and terms.** OpenStreetMap data is ODbL (keep attribution). Google Places content has caching restrictions (only the place ID is meant for permanent storage). Wikidata is CC0.
- **Company-level only.** The engine deliberately collects no personal contact data.

**Future improvements**

- Background jobs with progress (Celery/RQ or Postgres queues) and parallel enrichment
- Profile editor in the dashboard and A/B comparison of profiles
- More discovery sources (business registers, chambers of commerce, trade-fair lists)
- Learning weights from CRM outcomes, keeping the rules as the explainable baseline
- Multilingual offering detection with embeddings
- More CRM providers (Pipedrive, Salesforce)

---

## Licence

[MIT](LICENSE). The demo datasets are synthetic and fictional. Map data © OpenStreetMap contributors (ODbL).
