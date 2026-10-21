# AI Lead Intelligence Engine

[![CI](https://github.com/Adreas-AI/ai-lead-intelligence-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Adreas-AI/ai-lead-intelligence-engine/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

An end-to-end B2B lead engine for agri-food marketplaces. It finds companies, enriches them from their own websites, scores them with explainable rules (plus an optional LLM review), drafts outreach for a person to review, and exports the qualified leads to CSV or HubSpot.

The target customers it is built around are agricultural producers, cooperatives, food wholesalers, distributors and import/export traders: the supply and demand sides of a B2B agricultural marketplace.

> Outreach is never sent automatically. Every draft waits for human review.
> Missing company information is never invented. Gaps are recorded as `missing_fields`.

---

## Contents

- [Business problem and solution](#business-problem-and-solution)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Installation](#installation)
- [Environment configuration](#environment-configuration)
- [Demo](#demo)
- [Example API requests](#example-api-requests)
- [Screenshots](#screenshots)
- [How qualification works](#how-qualification-works)
- [Testing](#testing)
- [Business Impact & GTM Use Cases](#business-impact--gtm-use-cases)
- [Limitations and future improvements](#limitations-and-future-improvements)

---

## Business problem and solution

**Problem.** Building a B2B agricultural marketplace means finding thousands of producers, packers, wholesalers and importers across many countries and languages. Done by hand, prospect research is slow and inconsistent:

- Researchers open websites one by one and copy details into spreadsheets.
- "Is this a good lead?" depends on who is asking, so qualification can't be compared across people or markets.
- The same company arrives from several lists and ends up in the CRM twice.
- Outreach is either generic or depends on time-consuming manual personalisation.

**Solution.** The engine turns this into a repeatable pipeline:

| Stage | What happens | Output |
|---|---|---|
| Discovery | Pull companies from CSV, Wikidata (public, CC0) or Google Places; filter by country, industry, type and keywords | Leads with source URLs |
| Deduplication | Match on domain, legal name + country, or fuzzy name; fill blanks instead of creating duplicates | Clean lead list, import report |
| Enrichment | Fetch the official site (robots.txt respected), verify it belongs to the company, extract products, categories, B2B signals, location | Evidence with source URLs and timestamps |
| Qualification | Deterministic 0–100 score across 6 criteria, hard gates, optional LLM adjustment (±10) | Structured JSON: score, reasoning, confidence, status |
| Outreach | Draft a short email from verified facts only; guardrails reject invented numbers or claims | Drafts in a review queue |
| CRM | Export to CSV or upsert into HubSpot without creating duplicates; failures are logged | Synced records, failure log |
| Analytics | Funnel, conversion rates, breakdowns by country and industry, measured API/LLM cost | Streamlit dashboard and API |

---

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        CSV[CSV upload]
        WD[Wikidata SPARQL<br/>public, CC0]
        GP[Google Places API<br/>optional, paid]
        MOCK[Mock provider<br/>synthetic data]
    end

    subgraph API["FastAPI service"]
        ING[Ingestion and<br/>duplicate detection]
        ENR[Enrichment<br/>robots.txt-aware fetcher,<br/>HTML/JSON-LD extraction]
        SCO[Scoring engine<br/>rules + gates]
        LLM[(LLM layer<br/>mock / OpenAI)]
        OUT[Outreach drafts<br/>guardrails]
        CRM[CRM layer<br/>CSV / HubSpot]
        ANA[Analytics]
    end

    DB[(PostgreSQL)]
    DASH[Streamlit dashboard]
    N8N[n8n workflows<br/>optional]
    HUB[HubSpot]
    WEB[(Company websites)]

    CSV & WD & GP & MOCK --> ING --> DB
    ENR <--> WEB
    DB --> ENR --> DB
    DB --> SCO --> DB
    SCO -. optional review .-> LLM
    OUT -. optional drafting .-> LLM
    DB --> OUT --> DB
    DB --> CRM --> HUB
    DB --> ANA
    DASH --> API
    N8N --> API
```

Every external call goes through one HTTP client. It identifies itself with a descriptive User-Agent, rate-limits per host with a token bucket, and retries transient failures with exponential backoff and jitter, honouring `Retry-After`. Inbound API calls are rate-limited per client and can be protected with an API key.

```
app/
├── core/          config (pydantic-settings), structured logging, retry, rate limiting, HTTP client
├── db/            SQLAlchemy models and session
├── domain/        agri-food taxonomy (industries, company types, product categories, signals), normalisation
├── providers/     discovery (mock, wikidata, google_places), web fetching, LLM (mock, openai), CRM (hubspot)
├── services/      leads/dedup, extraction, enrichment, scoring, outreach, crm, analytics, usage
├── api/           FastAPI routes and dependencies
└── main.py        app factory and middleware (request IDs, rate limiting, access logs)
alembic/           database migrations
dashboard/         Streamlit internal dashboard
data/demo/         SYNTHETIC demo dataset (CSV, mock discovery results, website fixtures)
n8n/               importable n8n workflow
scripts/           demo runner and demo-data generator
tests/             unit and integration tests (no network, no keys)
```

**Data model.** `companies` (the lead, with its latest score and status for fast filtering) has many `enrichments`, `lead_scores`, `outreach_drafts` and `crm_sync_records`. Each stage appends a record, so history and provenance are kept. `import_batches` stores dedup statistics and `usage_events` meters API and LLM cost.

---

## Technology stack

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| API | FastAPI, Pydantic v2, pydantic-settings |
| Data | PostgreSQL 16, SQLAlchemy 2.0, Alembic |
| HTTP / parsing | httpx, BeautifulSoup |
| AI | OpenAI API (JSON mode), deterministic mock provider |
| Orchestration | n8n (optional) |
| Dashboard | Streamlit, Plotly, pandas |
| Logging | structlog (JSON in containers) |
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
| API and interactive docs | http://localhost:8000/docs |
| Dashboard | http://localhost:8501 |
| n8n (optional) | `docker compose --profile n8n up -d`, then http://localhost:5678 |

Migrations run automatically when the API container starts.

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

All settings live in `.env` (see [`.env.example`](.env.example)). Secrets are typed as `SecretStr`, so they never appear in logs or reprs. `.env` is git-ignored.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local PostgreSQL | SQLAlchemy URL |
| `API_KEY` | empty | If set, every request needs the `X-API-Key` header |
| `API_RATE_LIMIT_PER_MINUTE` | 120 | Inbound limit per client IP |
| `LLM_PROVIDER` | `mock` | `none`, `mock` or `openai` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | empty, `gpt-4o-mini` | Real LLM layer |
| `OPENAI_INPUT_COST_PER_1M`, `OPENAI_OUTPUT_COST_PER_1M` | 0.15 / 0.60 | Unit prices for cost reporting; check current pricing |
| `GOOGLE_PLACES_API_KEY` | empty | Enables Google Places discovery and website lookup |
| `HUBSPOT_ACCESS_TOKEN` | empty | Enables HubSpot sync (private-app token) |
| `ENRICHMENT_MODE` | `auto` | `offline` never touches the network |
| `OUTBOUND_REQUESTS_PER_SECOND` | 1.0 | Per-host politeness limit |
| `TARGET_COUNTRIES_PRIMARY` / `_SECONDARY` | EU27 / neighbours | Geographic fit |
| `QUALIFICATION_THRESHOLD` / `REVIEW_THRESHOLD` | 70 / 50 | Status cut-offs |
| `SENDER_NAME`, `SENDER_COMPANY`, `SENDER_VALUE_PROPOSITION` | demo values | Used in outreach drafts |

### Which integrations need credentials

| Integration | Needs credentials? | Without credentials |
|---|---|---|
| CSV import, dedup, scoring, outreach templates, CSV export, analytics | No | Fully functional |
| Mock discovery and mock LLM | No | Deterministic, $0 |
| **Wikidata discovery** | **No** (public SPARQL endpoint) | Real companies, fully functional |
| **Website enrichment** | **No** | Real websites fetched politely; synthetic `.example` sites come from fixtures |
| OpenAI LLM layer | `OPENAI_API_KEY` | Rule-based scoring and template drafts |
| Google Places discovery and website lookup | `GOOGLE_PLACES_API_KEY` | Provider reported as not configured (HTTP 503) |
| HubSpot sync | `HUBSPOT_ACCESS_TOKEN` | `/crm/sync` returns 503; CSV export still works |

---

## Demo

The demo uses **clearly labelled synthetic data**. Every company is fictional and uses the reserved `.example` domain (RFC 2606), served from local HTML fixtures. The dataset is built by [`scripts/generate_demo_data.py`](scripts/generate_demo_data.py) and deliberately includes:

- strong leads (cooperatives, exporters, wholesalers and importers in target markets)
- weak leads (a deli with an online shop, a restaurant, a farm-software SaaS)
- a company whose listed website belongs to someone else, so verification must fail
- an unreachable website, companies without websites, and an almost-empty record
- three duplicates (same domain, same legal name in another format, a near-identical name) and two invalid rows

Run it:

```bash
# with Docker Compose running:
docker compose exec api python scripts/run_demo.py --use-llm

# or locally:
python scripts/run_demo.py --use-llm          # --reset removes previous synthetic leads only
```

It goes through the seven steps and prints the results of each:

1. **Import leads** from `data/demo/synthetic_companies.csv` (29 rows: 24 created, 3 duplicates, 2 invalid)
2. **Detect duplicates**, including a mock-discovery result that duplicates a CSV lead
3. **Enrich** each company from its website (one fails on purpose, one is not verified)
4. **Score** with the per-criterion breakdown and reasoning
5. **Generate outreach drafts** for qualified leads (status `pending_review`)
6. **Export** qualified leads to `exports/qualified_leads_<timestamp>.csv`
7. **Show pipeline analytics** (funnel, conversion, average score, measured cost)

Then open the dashboard at http://localhost:8501 to filter leads, inspect explanations and approve or reject drafts.

**Real data.** Discovery works against real sources without any key:

```bash
curl -X POST localhost:8000/discovery/search -H 'Content-Type: application/json' \
  -d '{"provider":"wikidata","countries":["GR","CY"],"industries":["food_manufacturing","agriculture"],"limit":20}'
curl -X POST localhost:8000/pipeline/run -H 'Content-Type: application/json' -d '{}'
```

---

## Example API requests

The full OpenAPI documentation is at `/docs`. If `API_KEY` is set, add `-H "X-API-Key: $API_KEY"` to each request.

```bash
# Import a CSV (needs at least a "name" column; website/country/industry/... are optional)
curl -F "file=@data/demo/synthetic_companies.csv" localhost:8000/leads/import

# Create leads as JSON
curl -X POST localhost:8000/leads -H 'Content-Type: application/json' -d '[
  {"name":"Crete Avocado Growers","website":"https://crete-avocado.example",
   "country":"GR","industry":"Agriculture","company_type":"Grower"}]'

# Discover (mock | wikidata | google_places)
curl -X POST localhost:8000/discovery/search -H 'Content-Type: application/json' \
  -d '{"provider":"mock","countries":["GR","IT"],"limit":10}'

# Enrich, score (with optional LLM review) and draft, one stage at a time or all at once
curl -X POST localhost:8000/enrichment/run   -H 'Content-Type: application/json' -d '{"limit":100}'
curl -X POST localhost:8000/scoring/run      -H 'Content-Type: application/json' -d '{"use_llm":true}'
curl -X POST localhost:8000/outreach/generate -H 'Content-Type: application/json' -d '{}'
curl -X POST localhost:8000/pipeline/run     -H 'Content-Type: application/json' -d '{"use_llm":false}'

# Score one lead and get the structured result
curl -X POST "localhost:8000/leads/1/score?use_llm=true"

# Filter leads
curl "localhost:8000/leads?country=GR&qualification=qualified&min_score=70"

# Review a draft (approve / reject / edit)
curl -X PATCH localhost:8000/outreach/drafts/1 -H 'Content-Type: application/json' \
  -d '{"status":"approved","reviewer_notes":"OK to send manually"}'

# Export qualified leads / sync to HubSpot / inspect failures
curl -X POST localhost:8000/crm/export/csv -H 'Content-Type: application/json' -d '{}' -o qualified.csv
curl -X POST localhost:8000/crm/sync -H 'Content-Type: application/json' -d '{"provider":"hubspot"}'
curl "localhost:8000/crm/sync-records?status=failed"

# Analytics
curl localhost:8000/analytics/summary
```

Example scoring response (`POST /leads/{id}/score?use_llm=true`, synthetic demo lead, mock LLM), abridged:

```json
{
  "score": 92,
  "rule_score": 89,
  "llm_adjustment": 3,
  "confidence": 1.0,
  "status": "qualified",
  "reasoning": "Final score 92/100 (qualified). Rule-based score 89/100 (qualified). Strengths: industry 'agriculture'; Greece is a primary target market. LLM review (+3): Aegean Citrus Growers Cooperative lists Fresh fruit with business signals: wholesale, bulk_supply, export, import, b2b_explicit.",
  "breakdown": [
    {"criterion": "industry_relevance",         "points": 25, "max_points": 25, "reasons": ["industry 'agriculture'"]},
    {"criterion": "marketplace_compatibility",  "points": 18, "max_points": 20, "reasons": ["company type 'cooperative'"]},
    {"criterion": "geographic_fit",             "points": 15, "max_points": 15, "reasons": ["Greece is a primary target market"]},
    {"criterion": "b2b_business_model",         "points": 15, "max_points": 15, "reasons": ["'cooperative' is a B2B company type", "B2B signals: wholesale, bulk_supply, export, import, b2b_explicit"]},
    {"criterion": "product_category_relevance", "points": 6,  "max_points": 15, "reasons": ["core marketplace categories: Fresh fruit"]},
    {"criterion": "data_completeness",          "points": 10, "max_points": 10, "reasons": ["available: website, website verified, summary, country, products detected, source URL"]}
  ],
  "llm_analysis": {"model": "...", "adjustment": 3, "reasoning": "...", "risks": [], "recommended_next_step": "..."},
  "scoring_version": "rules-v1+llm"
}
```

---

## Screenshots

Screenshots of the demo dataset live in [`docs/screenshots/`](docs/screenshots/).

| Overview | Lead explanation | Outreach review |
|---|---|---|
| ![Overview](docs/screenshots/overview.png) | ![Lead detail](docs/screenshots/lead_detail.png) | ![Outreach review](docs/screenshots/outreach.png) |

To regenerate them: run the demo, open the dashboard and capture the three tabs.

---

## How qualification works

**Layer 1: deterministic rules** (always on, same input gives the same output). Every point comes with a reason.

| Criterion | Max | Based on |
|---|---|---|
| Industry relevance | 25 | Canonical industry; product evidence partly substitutes for an unknown industry |
| Marketplace compatibility | 20 | Company type (exporter, wholesaler, cooperative, ... vs retailer, restaurant, SaaS) |
| Geographic fit | 15 | Primary / secondary / other target markets (configurable) |
| B2B business model | 15 | B2B company type plus website signals (wholesale, bulk, export, HORECA, MOQ, private label, certifications); consumer-only shops are penalised |
| Product category relevance | 15 | Core marketplace categories (fruit, vegetables, olive oil, grains, legumes, nuts, herbs, honey) vs adjacent ones |
| Data completeness | 10 | Website, verification, summary, country, products, source URL |

**Statuses.** `qualified` ≥ 70, `needs_review` ≥ 50, `disqualified` < 50, and `insufficient_data` when completeness is below 4/10.

**Hard gates.** A lead without a verified official website, or outside the target markets, is never auto-qualified. It is held at `needs_review` for a person to decide.

**Confidence** reflects how much verified evidence the score is based on, not how high the score is.

**Layer 2: optional LLM review.** The LLM gets the same verified facts plus the rule breakdown. It returns `adjustment` (clamped to ±10), reasoning, risks and a next step as JSON. If the LLM fails or returns invalid JSON, the rule score stands and the failure is recorded.

**Outreach guardrails.** LLM drafts are rejected and replaced by the template if they leave out the company name, contain placeholders, include numbers that aren't in the facts, use hype or guarantee language, or mention certifications such as "organic" or "ISO" that weren't verified. Facts from a website that wasn't verified are excluded from personalisation.

**Responsible enrichment.** The engine respects robots.txt (RFC 9309: 5xx or unreachable means disallow), identifies itself, rate-limits per host, and never bypasses anti-bot measures. A 401, 403 or 429 response, or a CAPTCHA/challenge page, is recorded as `blocked`.

---

## Testing

```bash
pytest                                   # 98 tests, no network, no API keys (SQLite in-memory)
pytest --cov=app --cov-report=term-missing
DATABASE_URL=postgresql+psycopg://leads:leads@localhost:5432/leads pytest   # against PostgreSQL
ruff check . && ruff format --check .
```

- **Unit tests:** normalisation, taxonomy, HTML extraction, scoring rules and gates, LLM clamping and fallback, outreach guardrails, retry/backoff, rate limiters, CSV parsing and dedup, and every provider (Wikidata, Google Places, website fetcher incl. robots.txt and anti-bot handling, OpenAI, HubSpot) with mocked HTTP.
- **Integration tests:** the full API flow (import → enrich → score → draft → review → export → analytics), idempotent re-runs, validation errors, CRM duplicate prevention and failure recording, API-key auth and rate limiting.
- **CI** (GitHub Actions): lint, tests on SQLite and PostgreSQL, `alembic upgrade/check/downgrade`, the end-to-end demo, and a Docker build.

---

## Business Impact & GTM Use Cases

This section explains what the system is designed to change and how to measure whether it does. **No performance or ROI figures are claimed here.** The demo runs on synthetic data, so its numbers say nothing about real-world results.

### What it is designed to improve

**Less manual prospect research.** Collecting a company's website, location, product categories and business model, and deciding whether it is B2B, is the repetitive part of research. The engine does this from the company's own website and keeps the source URLs, so a researcher checks evidence instead of gathering it.

**Standardised qualification.** The same rules apply to every lead in every market, and every point is explained. Sales, marketplace and country teams can argue about *weights* (code-reviewed in [`app/domain/taxonomy.py`](app/domain/taxonomy.py) and [`app/services/scoring.py`](app/services/scoring.py)) instead of individual opinions. Hard gates keep uncertain leads with a person.

**Support for marketplace expansion.** Geographic fit, the company-type taxonomy and the product categories are configurable, so opening a new country or category means changing configuration rather than retraining researchers. The engine scores both sides of the marketplace: supply (producers, cooperatives, exporters) and demand (wholesalers, importers, distributors). Outreach is framed for each side.

**Cleaner CRM.** Domain and name-based deduplication on import, plus domain lookup and stored external IDs on sync, are designed to prevent duplicate CRM records.

### GTM use cases

1. **Market entry research.** Discover and qualify producers and buyers in a new country before a launch.
2. **Category expansion.** Find suppliers for a new product category, such as nuts and dried fruit, and rank them.
3. **Inbound list triage.** Score trade-fair or partner lists (CSV) in one batch and route `needs_review` leads to people.
4. **CRM hygiene.** Re-enrich existing accounts, flag near-duplicates (`GET /leads/duplicates`) and fill missing fields with evidence.
5. **SDR preparation.** Give SDRs a reviewed draft and the verified facts behind it, so personalisation stays accurate.
6. **Scheduled pipeline.** The included n8n workflow runs discovery and qualification every week and reminds the team about drafts waiting for review.

### How to measure actual results

Run a controlled comparison and report what you observe:

| Question | Metric | How to measure |
|---|---|---|
| Does it save research time? | Minutes per researched lead | Time a baseline sample researched manually; time the same number with the engine (review time included). Compare medians. |
| Is enrichment accurate? | Precision of extracted fields | Have a reviewer check a random sample (e.g. 50 leads) for website, country, categories and B2B model; record the % correct per field. |
| Is scoring useful? | Agreement with experts; conversion by score band | Ask two or three people to label a sample (qualified / not); compute agreement with the engine. Then track reply and meeting rates per score band in the CRM. |
| Is qualification consistent? | Inter-rater agreement before/after | Compare how often people agree with each other vs how often they agree with the engine on the same sample. |
| Does outreach perform? | Reply rate, positive reply rate, edit rate | Track approved drafts in the CRM; record how much reviewers edit drafts (`reviewer_notes`, edited body). |
| Is the CRM cleaner? | Duplicate rate | Measure duplicate companies per 1,000 records before and after; compare with `duplicates_blocked` in analytics. |
| What does it cost? | $ per qualified lead | `GET /analytics/summary` → `total_cost_usd` (from token and request counts and your configured prices) ÷ qualified leads. |

The API already exposes pipeline counts, conversion rates between stages, duplicates blocked and measured API/LLM cost (`/analytics/summary`, dashboard *Overview*). Outcome metrics such as replies, meetings and deals belong in the CRM and should be joined on the exported `company_id` or CRM ID.

---

## Limitations and future improvements

**Current limitations**

- **Keyword taxonomy in English.** Category and signal detection uses English keywords. Sites only in Greek, Spanish or Italian give fewer signals. The engine prefers a site's own `hreflang="en"` version when there is one, but multilingual keyword sets (or embeddings) are the next step.
- **Shallow crawling.** Only the homepage, an English alternate and one "about"/"products" page are read. That is intentional for politeness, but some facts will be missed.
- **Coverage of public sources.** Wikidata mostly covers larger or notable companies. Small farms and traders need commercial B2B datasets, chambers of commerce, trade-fair lists or Google Places.
- **Google Places terms.** Google's terms limit how long Places content may be cached. Only the place ID (`source_ref`) is meant for permanent storage, so review the terms before storing results long-term.
- **Synchronous batches.** Pipeline runs execute inside the API request. That is fine for hundreds of leads; larger volumes need a job queue.
- **Fuzzy dedup at import** compares names within a country and with the same first token. Very differently written names of the same company can still slip through; `GET /leads/duplicates` surfaces candidates for review.
- **Contacts.** The engine works at company level and deliberately doesn't collect personal data about individuals.

**Future improvements**

- Background workers (e.g. Celery/RQ or Postgres-based queues) with progress tracking
- Multilingual taxonomy (EL/ES/IT/FR/DE) and embedding-based category matching
- Additional sources: Companies House, OpenCorporates, EU trade registers, trade-fair exhibitor lists
- Feedback loop: learn weights from CRM outcomes and keep rules as the explainable baseline
- Per-market scoring profiles and A/B testing of outreach templates
- GDPR tooling: retention policies and data-subject request handling for any contact data
- Pipedrive / Salesforce CRM providers

---

## Licence

[MIT](LICENSE). The demo dataset is synthetic and fictional.
