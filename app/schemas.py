"""Pydantic schemas: request validation and API response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.geo import normalize_country
from app.domain.normalize import extract_domain, normalize_url


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Leads -------------------------------------------------------------------------
class CompanyIn(BaseModel):
    """A lead as it arrives from CSV, discovery providers or the API."""

    name: str = Field(min_length=2, max_length=255)
    website: str | None = Field(default=None, max_length=512)
    country: str | None = None
    city: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=255)
    company_type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=5000)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    source: str = Field(default="manual", max_length=64)
    source_url: str | None = Field(default=None, max_length=1024)
    source_ref: str | None = Field(default=None, max_length=255)
    is_synthetic: bool = False
    profile: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("name is too short")
        return v

    @field_validator("website")
    @classmethod
    def _valid_website(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        normalized = normalize_url(v)
        if normalized is None:
            raise ValueError(f"invalid website URL: {v!r}")
        if extract_domain(normalized) is None:
            return None  # social profile / webmail / marketplace page: not an official site
        return normalized

    @field_validator("country")
    @classmethod
    def _valid_country(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        code = normalize_country(v)
        if code is None:
            raise ValueError(f"unrecognised country: {v!r} (use ISO-3166 alpha-2, e.g. 'GR')")
        return code

    @field_validator("keywords", mode="before")
    @classmethod
    def _split_keywords(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [k.strip() for k in v.replace(";", ",").split(",") if k.strip()]
        return v or []


class CompanyOut(ORMModel):
    id: int
    name: str
    website: str | None
    domain: str | None
    country: str | None
    city: str | None
    industry: str
    industry_raw: str | None
    company_type: str
    description: str | None
    keywords: list[str]
    source: str
    source_url: str | None
    is_synthetic: bool
    profile: str
    status: str
    score: int | None
    qualification_status: str | None
    last_enriched_at: datetime | None
    last_scored_at: datetime | None
    created_at: datetime


class EnrichmentOut(ORMModel):
    id: int
    status: str
    website_url: str | None
    website_verified: bool
    page_title: str | None
    meta_description: str | None
    summary: str | None
    summary_method: str | None
    products: list[str]
    categories: list[str]
    business_signals: list[str]
    negative_signals: list[str]
    inferred_company_type: str | None
    detected_country: str | None
    country_evidence: str | None
    location: str | None
    markets_mentioned: list[str]
    source_urls: list[str]
    missing_fields: list[str]
    error: str | None
    enriched_at: datetime


class CriterionScore(BaseModel):
    criterion: str
    points: int
    max_points: int
    reasons: list[str]


class ScoreOut(ORMModel):
    """The structured qualification result returned by the scoring engine."""

    id: int
    company_id: int
    profile: str
    score: int
    rule_score: int
    llm_adjustment: int
    confidence: float
    status: str
    breakdown: list[CriterionScore]
    reasoning: str
    llm_analysis: dict[str, Any] | None
    scoring_version: str
    created_at: datetime


class DraftOut(ORMModel):
    id: int
    company_id: int
    subject: str
    body: str
    generation_method: str
    facts_used: dict[str, Any]
    warnings: list[str]
    status: str
    reviewer_notes: str | None
    created_at: datetime
    reviewed_at: datetime | None


class DraftReview(BaseModel):
    status: Literal["pending_review", "approved", "rejected"]
    reviewer_notes: str | None = Field(default=None, max_length=2000)
    subject: str | None = Field(default=None, min_length=3, max_length=255)
    body: str | None = Field(default=None, min_length=20, max_length=5000)


class CrmSyncOut(ORMModel):
    id: int
    company_id: int
    provider: str
    external_id: str | None
    status: str
    error: str | None
    attempts: int
    last_attempt_at: datetime
    last_success_at: datetime | None


class CompanyDetail(CompanyOut):
    enrichment: EnrichmentOut | None = None
    latest_score: ScoreOut | None = None
    drafts: list[DraftOut] = []
    crm_syncs: list[CrmSyncOut] = []


class StatusUpdate(BaseModel):
    status: Literal["contacted", "archived", "needs_review", "qualified", "disqualified"]


class LeadPage(BaseModel):
    total: int
    items: list[CompanyOut]


# --- Import / dedup ---------------------------------------------------------------
class DuplicateMatch(BaseModel):
    row: int | None = None
    name: str
    matched_company_id: int
    matched_name: str
    reason: str
    similarity: float


class RowError(BaseModel):
    row: int
    error: str


class ImportReport(BaseModel):
    batch_id: int
    source: str
    profile: str
    total_rows: int
    created: int
    duplicates: list[DuplicateMatch]
    invalid: list[RowError]
    created_ids: list[int]


class PossibleDuplicate(BaseModel):
    company_id: int
    company_name: str
    other_id: int
    other_name: str
    country: str | None
    similarity: float


# --- Discovery --------------------------------------------------------------------
class DiscoveryRequest(BaseModel):
    provider: str = Field(
        default="mock", examples=["openstreetmap", "google_places", "wikidata", "mock"]
    )
    profile: str | None = Field(default=None, description="ICP profile (default: DEFAULT_PROFILE)")
    countries: list[str] = Field(default_factory=list, max_length=30)
    cities: list[str] = Field(default_factory=list, max_length=10)
    radius_km: float = Field(default=5.0, gt=0, le=50, description="Search radius around cities")
    require_website: bool = Field(default=True, description="Only businesses with a website")
    industries: list[str] = Field(default_factory=list, max_length=20)
    company_types: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=25, ge=1, le=200)
    save: bool = True

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        codes = []
        for c in v:
            code = normalize_country(c)
            if code is None:
                raise ValueError(f"unrecognised country: {c!r}")
            codes.append(code)
        return codes


class ProviderInfo(BaseModel):
    name: str
    description: str
    requires_credentials: bool
    configured: bool


class DiscoveryResponse(BaseModel):
    provider: str
    profile: str
    found: int
    results: list[CompanyIn]
    import_report: ImportReport | None = None


# --- Pipeline runs ------------------------------------------------------------------
class BatchRequest(BaseModel):
    company_ids: list[int] | None = Field(default=None, max_length=500)
    limit: int = Field(default=50, ge=1, le=500)
    force: bool = False


class ScoringRequest(BatchRequest):
    use_llm: bool = False


class OutreachRequest(BatchRequest):
    use_llm: bool = False
    include_needs_review: bool = False


class CrmSyncRequest(BaseModel):
    provider: Literal["hubspot"] = "hubspot"
    company_ids: list[int] | None = None
    limit: int = Field(default=100, ge=1, le=500)


class CsvExportRequest(BaseModel):
    include_needs_review: bool = False
    only_new: bool = False


class BatchResult(BaseModel):
    processed: int
    succeeded: int
    failed: int
    skipped: int = 0
    details: list[dict[str, Any]] = []


class PipelineRequest(BaseModel):
    enrich: bool = True
    score: bool = True
    draft: bool = True
    use_llm: bool = False
    limit: int = Field(default=100, ge=1, le=500)


class PipelineResult(BaseModel):
    enrichment: BatchResult | None
    scoring: BatchResult | None
    outreach: BatchResult | None


# --- Analytics --------------------------------------------------------------------
class CountItem(BaseModel):
    key: str
    count: int


class CostItem(BaseModel):
    provider: str
    operation: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class AnalyticsSummary(BaseModel):
    total_leads: int
    enriched_leads: int
    scored_leads: int
    qualified_leads: int
    needs_review_leads: int
    disqualified_leads: int
    insufficient_data_leads: int
    drafts_generated: int
    drafts_approved: int
    crm_synced: int
    crm_failures: int
    duplicates_blocked: int
    average_score: float | None
    conversion: dict[str, float | None]
    by_country: list[CountItem]
    by_industry: list[CountItem]
    by_status: list[CountItem]
    by_source: list[CountItem]
    by_profile: list[CountItem]
    score_distribution: list[CountItem]
    costs: list[CostItem]
    total_cost_usd: float


# --- Free-text search -----------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(
        min_length=2, max_length=200, examples=["κρεοπωλεία", "dental clinics", "vegan restaurants"]
    )
    country: str = Field(default="GR", description="ISO code or country name")
    city: str | None = Field(default=None, max_length=100, examples=["Αθήνα", "Thessaloniki"])
    radius_km: float = Field(default=5.0, gt=0, le=50)
    limit: int = Field(default=30, ge=1, le=200)
    require_website: bool = Field(
        default=True, description="Only businesses with a website (needed for enrichment)"
    )
    offer: str | None = Field(
        default=None, max_length=500, description="What you sell (used in outreach drafts)"
    )
    use_llm: bool | None = Field(
        default=None, description="Interpret with the LLM (default: when LLM_PROVIDER=openai)"
    )

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        code = normalize_country(v)
        if code is None:
            raise ValueError(f"unrecognised country: {v!r}")
        return code


class SearchPlan(BaseModel):
    """What the engine understood from the query - shown to the user before searching."""

    query: str
    label: str
    method: Literal["catalog", "llm", "name_match"]
    explanation: str
    categories: list[str] = []
    osm_tags: list[str] = []
    name_keywords: list[str] = []
    search_terms: list[str] = []
    website_keywords: list[str] = []
    country: str
    city: str | None = None
    radius_km: float = 5.0
    profile_name: str
    sources: list[str] = []
    warnings: list[str] = []


class SearchRunRequest(SearchRequest):
    enrich_and_score: bool = True


class SearchRunResult(BaseModel):
    plan: SearchPlan
    discovery: list[DiscoveryResponse]
    pipeline: PipelineResult | None = None
    leads: list[CompanyOut] = []
