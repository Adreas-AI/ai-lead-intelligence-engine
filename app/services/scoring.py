"""Explainable lead qualification.

Layer 1 (always): deterministic rules over verified facts -> 0..100 with a
per-criterion breakdown. Same input, same output; every point has a reason.

Layer 2 (optional): an LLM reviews the same facts and may adjust the score by at
most +/-10 points, with written reasoning. If the LLM fails, the rule score stands.

Weights (max points): industry 25, marketplace fit 20, geography 15,
B2B model 15, product categories 15, data completeness 10.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import (
    Company,
    EnrichmentStatus,
    LeadScore,
    LeadStatus,
    QualificationStatus,
)
from app.domain.taxonomy import COMPANY_TYPES, COUNTRY_NAMES, INDUSTRIES, PRODUCT_CATEGORIES
from app.providers.llm import LLMError, LLMProvider
from app.schemas import BatchResult, CriterionScore
from app.services.usage import record_usage

log = get_logger(__name__)

SCORING_VERSION = "rules-v1"
LLM_MAX_ADJUSTMENT = 10
_CATEGORY_BY_LABEL = {c.label: c for c in PRODUCT_CATEGORIES}

SCORING_SYSTEM_PROMPT = f"""You are a B2B lead qualification analyst for an agricultural
marketplace that connects producers, wholesalers, exporters and food distributors with
professional buyers. You receive verified facts about one company and the result of a
deterministic scoring model.

Rules:
- Use ONLY the supplied facts. Never assume products, size, customers or locations.
- If facts are thin, say so and do not compensate with assumptions.
- You may adjust the rule score by an integer between -{LLM_MAX_ADJUSTMENT} and
  +{LLM_MAX_ADJUSTMENT}, only when the facts justify it.

Respond with JSON only:
{{"adjustment": int, "reasoning": string (max 3 sentences),
  "risks": [string], "recommended_next_step": string}}"""


@dataclass
class LeadFacts:
    name: str
    industry: str
    company_type: str
    country: str | None
    website: str | None
    website_verified: bool = False
    enrichment_status: str | None = None
    summary: str | None = None
    categories: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    business_signals: list[str] = field(default_factory=list)
    b2c_signals: list[str] = field(default_factory=list)
    markets_mentioned: list[str] = field(default_factory=list)
    has_source_url: bool = False

    @classmethod
    def from_company(cls, company: Company) -> LeadFacts:
        e = company.latest_enrichment
        return cls(
            name=company.name,
            industry=company.industry,
            company_type=company.company_type,
            country=company.country or (e.detected_country if e else None),
            website=company.website,
            website_verified=bool(e and e.website_verified),
            enrichment_status=e.status if e else None,
            summary=e.summary if e else company.description,
            categories=list(e.categories) if e else [],
            products=list(e.products) if e else [],
            business_signals=list(e.business_signals) if e else [],
            b2c_signals=list(e.b2c_signals or []) if e else [],
            markets_mentioned=list(e.markets_mentioned) if e else [],
            has_source_url=bool(company.source_url or (e and e.source_urls)),
        )


@dataclass
class RuleResult:
    score: int
    breakdown: list[CriterionScore]
    confidence: float
    status: QualificationStatus
    reasoning: str
    completeness: int
    gates: list[str] = field(default_factory=list)


def _industry(f: LeadFacts) -> CriterionScore:
    points = INDUSTRIES.get(f.industry, (0, ()))[0]
    if f.industry in ("unknown", "other") and f.categories:
        # Product evidence partially substitutes for a missing/unclear industry label.
        core = any(_CATEGORY_BY_LABEL[c].core for c in f.categories if c in _CATEGORY_BY_LABEL)
        points = 15 if core else 8
        reason = f"industry '{f.industry}', but product evidence suggests agri-food activity"
    elif f.industry == "unknown":
        reason = "industry unknown"
    else:
        reason = f"industry '{f.industry}'"
    return CriterionScore(
        criterion="industry_relevance", points=points, max_points=25, reasons=[reason]
    )


def _marketplace_fit(f: LeadFacts) -> CriterionScore:
    points, _, _ = COMPANY_TYPES.get(f.company_type, (0, False, ()))
    reasons = [f"company type '{f.company_type}'"]
    if f.company_type == "unknown" and f.business_signals:
        points = 10
        reasons = ["company type unknown; B2B trade signals present"]
    return CriterionScore(
        criterion="marketplace_compatibility", points=points, max_points=20, reasons=reasons
    )


def _geography(f: LeadFacts, settings: Settings) -> CriterionScore:
    if not f.country:
        return CriterionScore(
            criterion="geographic_fit", points=0, max_points=15, reasons=["country unknown"]
        )
    name = COUNTRY_NAMES.get(f.country, f.country)
    if f.country in settings.primary_countries:
        return CriterionScore(
            criterion="geographic_fit",
            points=15,
            max_points=15,
            reasons=[f"{name} is a primary target market"],
        )
    if f.country in settings.secondary_countries:
        return CriterionScore(
            criterion="geographic_fit",
            points=9,
            max_points=15,
            reasons=[f"{name} is a secondary target market"],
        )
    return CriterionScore(
        criterion="geographic_fit",
        points=3,
        max_points=15,
        reasons=[f"{name} is outside target markets"],
    )


def _b2b(f: LeadFacts) -> CriterionScore:
    is_b2b_type = COMPANY_TYPES.get(f.company_type, (0, False, ()))[1]
    points = 6 if is_b2b_type else 0
    reasons = [f"'{f.company_type}' is a B2B company type"] if is_b2b_type else []
    if f.business_signals:
        points += 3 * len(f.business_signals)
        reasons.append("B2B signals: " + ", ".join(f.business_signals))
    if f.b2c_signals and not f.business_signals:
        points -= 5
        reasons.append("consumer-only signals: " + ", ".join(f.b2c_signals))
    if not reasons:
        reasons.append("no B2B evidence found")
    return CriterionScore(
        criterion="b2b_business_model",
        points=max(0, min(15, points)),
        max_points=15,
        reasons=reasons,
    )


def _products(f: LeadFacts) -> CriterionScore:
    core = [c for c in f.categories if c in _CATEGORY_BY_LABEL and _CATEGORY_BY_LABEL[c].core]
    adjacent = [
        c for c in f.categories if c in _CATEGORY_BY_LABEL and not _CATEGORY_BY_LABEL[c].core
    ]
    points = min(15, 6 * len(core) + 3 * len(adjacent))
    reasons = []
    if core:
        reasons.append("core marketplace categories: " + ", ".join(core))
    if adjacent:
        reasons.append("adjacent categories: " + ", ".join(adjacent))
    if not reasons:
        reasons.append("no relevant product categories detected")
    return CriterionScore(
        criterion="product_category_relevance", points=points, max_points=15, reasons=reasons
    )


def _completeness(f: LeadFacts) -> CriterionScore:
    checks = {
        "website": (2, bool(f.website)),
        "website verified": (2, f.website_verified),
        "summary": (2, bool(f.summary)),
        "country": (1, bool(f.country)),
        "products detected": (2, bool(f.categories)),
        "source URL": (1, f.has_source_url),
    }
    points = sum(p for p, ok in checks.values() if ok)
    present = [k for k, (_, ok) in checks.items() if ok]
    missing = [k for k, (_, ok) in checks.items() if not ok]
    reasons = []
    if present:
        reasons.append("available: " + ", ".join(present))
    if missing:
        reasons.append("missing: " + ", ".join(missing))
    return CriterionScore(
        criterion="data_completeness", points=points, max_points=10, reasons=reasons
    )


def qualification_gates(f: LeadFacts, settings: Settings) -> list[str]:
    """Hard requirements for automatic qualification. A failed gate caps the
    status at needs_review: a human decides, regardless of the score."""
    gates = []
    if not f.website_verified:
        gates.append("no verified official website")
    if f.country and f.country not in settings.primary_countries | settings.secondary_countries:
        gates.append("outside target markets")
    return gates


def classify(
    score: int, completeness: int, settings: Settings, gates: list[str] | None = None
) -> QualificationStatus:
    if completeness < 4:
        return QualificationStatus.INSUFFICIENT_DATA
    if score >= settings.qualification_threshold:
        return QualificationStatus.NEEDS_REVIEW if gates else QualificationStatus.QUALIFIED
    if score >= settings.review_threshold:
        return QualificationStatus.NEEDS_REVIEW
    return QualificationStatus.DISQUALIFIED


def score_lead(f: LeadFacts, settings: Settings) -> RuleResult:
    breakdown = [
        _industry(f),
        _marketplace_fit(f),
        _geography(f, settings),
        _b2b(f),
        _products(f),
        _completeness(f),
    ]
    score = sum(c.points for c in breakdown)
    completeness = breakdown[-1].points
    enriched_ok = f.enrichment_status in (EnrichmentStatus.SUCCESS, EnrichmentStatus.PARTIAL)
    confidence = round(min(1.0, 0.25 + 0.55 * completeness / 10 + (0.2 if enriched_ok else 0)), 2)
    gates = qualification_gates(f, settings)
    status = classify(score, completeness, settings, gates)

    strongest = sorted(breakdown, key=lambda c: c.points / c.max_points, reverse=True)
    top = [c for c in strongest if c.points / c.max_points >= 0.6][:2]
    weak = [c for c in strongest[::-1] if c.points / c.max_points < 0.4][:2]
    parts = [f"Rule-based score {score}/100 ({status.value})."]
    if top:
        parts.append("Strengths: " + "; ".join(c.reasons[0] for c in top) + ".")
    if weak:
        parts.append("Gaps: " + "; ".join(c.reasons[-1] for c in weak) + ".")
    if gates and score >= settings.qualification_threshold:
        parts.append("Held for human review: " + "; ".join(gates) + ".")
    return RuleResult(score, breakdown, confidence, status, " ".join(parts), completeness, gates)


class ScoringService:
    def __init__(self, db: Session, settings: Settings, llm: LLMProvider | None = None):
        self.db = db
        self.settings = settings
        self.llm = llm

    def _llm_review(self, facts: LeadFacts, rule: RuleResult) -> tuple[int, dict | None]:
        assert self.llm is not None
        payload = json.dumps(
            {
                "facts": asdict(facts),
                "rule_result": {
                    "rule_score": rule.score,
                    "status": rule.status.value,
                    "breakdown": [c.model_dump() for c in rule.breakdown],
                },
            }
        )
        try:
            result = self.llm.complete_json(
                SCORING_SYSTEM_PROMPT, payload, operation="lead_scoring"
            )
        except LLMError as exc:
            record_usage(
                self.db,
                provider=self.llm.name,
                operation="lead_scoring",
                model=self.llm.model,
                success=False,
            )
            log.warning("llm_scoring_failed", company=facts.name, error=str(exc))
            return 0, {"error": str(exc)}
        record_usage(
            self.db,
            provider=self.llm.name,
            operation="lead_scoring",
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=self.llm.cost_usd(result),
        )
        data = result.data
        try:
            adjustment = int(data.get("adjustment", 0))
        except (TypeError, ValueError):
            adjustment = 0
        adjustment = max(-LLM_MAX_ADJUSTMENT, min(LLM_MAX_ADJUSTMENT, adjustment))
        analysis = {
            "model": result.model,
            "adjustment": adjustment,
            "reasoning": str(data.get("reasoning", ""))[:1000],
            "risks": [str(r)[:300] for r in data.get("risks", []) if r][:5],
            "recommended_next_step": str(data.get("recommended_next_step", ""))[:300],
        }
        return adjustment, analysis

    def score(self, company: Company, *, use_llm: bool = False) -> LeadScore:
        facts = LeadFacts.from_company(company)
        rule = score_lead(facts, self.settings)
        adjustment, analysis = 0, None
        if (
            use_llm
            and self.llm is not None
            and rule.status != QualificationStatus.INSUFFICIENT_DATA
        ):
            adjustment, analysis = self._llm_review(facts, rule)
        final = max(0, min(100, rule.score + adjustment))
        status = classify(final, rule.completeness, self.settings, rule.gates)
        reasoning = rule.reasoning
        if analysis and "error" not in analysis:
            reasoning = f"Final score {final}/100 ({status.value}). " + reasoning
        if rule.gates and final >= self.settings.qualification_threshold > rule.score:
            reasoning += " Held for human review: " + "; ".join(rule.gates) + "."
        if analysis and analysis.get("reasoning"):
            reasoning += f" LLM review ({adjustment:+d}): {analysis['reasoning']}"

        record = LeadScore(
            company_id=company.id,
            score=final,
            rule_score=rule.score,
            llm_adjustment=adjustment,
            confidence=rule.confidence,
            status=status.value,
            breakdown=[c.model_dump() for c in rule.breakdown],
            reasoning=reasoning,
            llm_analysis=analysis,
            scoring_version=SCORING_VERSION + ("+llm" if analysis else ""),
        )
        self.db.add(record)
        company.score = final
        company.qualification_status = status.value
        company.last_scored_at = datetime.now(UTC)
        if company.status not in (
            LeadStatus.SYNCED_TO_CRM,
            LeadStatus.CONTACTED,
            LeadStatus.OUTREACH_DRAFTED,
        ):
            company.status = status.value
        self.db.flush()
        return record

    def run_batch(
        self,
        company_ids: list[int] | None = None,
        *,
        limit: int = 50,
        force: bool = False,
        use_llm: bool = False,
    ) -> BatchResult:
        stmt = select(Company).where(Company.status != LeadStatus.ARCHIVED)
        if company_ids:
            stmt = stmt.where(Company.id.in_(company_ids))
        if not force:
            stmt = stmt.where(Company.last_scored_at.is_(None))
        companies = list(self.db.scalars(stmt.order_by(Company.id).limit(limit)))
        result = BatchResult(processed=0, succeeded=0, failed=0)
        for company in companies:
            result.processed += 1
            try:
                record = self.score(company, use_llm=use_llm)
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                log.exception("scoring_crashed", company_id=company.id)
                result.failed += 1
                result.details.append({"company_id": company.id, "error": str(exc)})
                continue
            result.succeeded += 1
            result.details.append(
                {"company_id": company.id, "score": record.score, "status": record.status}
            )
        return result
