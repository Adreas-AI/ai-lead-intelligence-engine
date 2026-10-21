"""Explainable lead qualification against an Ideal Customer Profile.

Layer 1 (always): deterministic rules over verified facts -> 0..100 with a
per-criterion breakdown. Same input, same output; every point has a reason.
Criteria and their weights come from the lead's profile (defaults: industry 25,
business type 20, geography 15, business signals 15, offering 15, completeness 10).

Layer 2 (optional): an LLM reviews the same facts and may adjust the score by at
most +/-10 points, with written reasoning. If the LLM fails, the rule score stands.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Company, EnrichmentStatus, LeadScore, LeadStatus, QualificationStatus
from app.domain.geo import COUNTRY_NAMES
from app.domain.keywords import fold
from app.domain.profiles import Profile, ProfileError, get_profile
from app.providers.llm import LLMError, LLMProvider
from app.schemas import BatchResult, CriterionScore
from app.services.usage import record_usage

log = get_logger(__name__)

SCORING_VERSION = "rules-v2"
LLM_MAX_ADJUSTMENT = 10

SCORING_SYSTEM_PROMPT = f"""You are a lead qualification analyst. You receive the ideal
customer profile (ICP) a salesperson is targeting, verified facts about one business and
the result of a deterministic scoring model.

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
    city: str | None = None
    website_verified: bool = False
    enrichment_status: str | None = None
    summary: str | None = None
    categories: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    business_signals: list[str] = field(default_factory=list)
    negative_signals: list[str] = field(default_factory=list)
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
            city=company.city,
            website=company.website,
            website_verified=bool(e and e.website_verified),
            enrichment_status=e.status if e else None,
            summary=e.summary if e else company.description,
            categories=list(e.categories) if e else [],
            products=list(e.products) if e else [],
            business_signals=list(e.business_signals) if e else [],
            negative_signals=list(e.negative_signals or []) if e else [],
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
    disqualified_by: list[str] = field(default_factory=list)


def _pts(share: float, weight: int) -> int:
    return max(0, min(weight, round(share * weight)))


def _industry(f: LeadFacts, p: Profile) -> CriterionScore:
    w = p.weights.industry
    item = p.industries.get(f.industry)
    if item:
        return CriterionScore(
            criterion="industry_relevance",
            points=_pts(item.fit, w),
            max_points=w,
            reasons=[f"industry: {item.label}"],
        )
    core = any((c := p.category(label)) and c.core for label in f.categories)
    if f.categories:
        # Offering evidence partially substitutes for a missing/unclear industry label.
        share = 0.6 if core else 0.32
        reason = f"industry '{f.industry}', but the website shows relevant offerings"
        return CriterionScore(
            criterion="industry_relevance", points=_pts(share, w), max_points=w, reasons=[reason]
        )
    reason = "industry unknown" if f.industry == "unknown" else "industry outside the profile"
    return CriterionScore(criterion="industry_relevance", points=0, max_points=w, reasons=[reason])


def _business_type(f: LeadFacts, p: Profile) -> CriterionScore:
    w = p.weights.business_type
    t = p.type_info(f.company_type)
    if t:
        return CriterionScore(
            criterion="business_type_fit",
            points=_pts(t.fit, w),
            max_points=w,
            reasons=[f"business type: {t.label}"],
        )
    if f.business_signals:
        return CriterionScore(
            criterion="business_type_fit",
            points=_pts(0.5, w),
            max_points=w,
            reasons=["business type unknown; relevant website signals present"],
        )
    return CriterionScore(
        criterion="business_type_fit", points=0, max_points=w, reasons=["business type unknown"]
    )


def _geography(f: LeadFacts, p: Profile) -> CriterionScore:
    w = p.weights.geography
    if not f.country:
        return CriterionScore(
            criterion="geographic_fit", points=0, max_points=w, reasons=["country unknown"]
        )
    name = COUNTRY_NAMES.get(f.country, f.country)
    cities = {fold(c) for c in p.geography.cities}
    if f.country in p.primary_countries():
        if not cities:
            share, reason = 1.0, f"{name} is a primary target market"
        elif f.city and fold(f.city) in cities:
            share, reason = 1.0, f"{f.city} is a target city"
        else:
            where = f.city or "city unknown"
            share, reason = 0.6, f"{name} is a target market ({where}, not a focus city)"
    elif f.country in p.secondary_countries():
        share, reason = 0.6, f"{name} is a secondary target market"
    else:
        share, reason = 0.2, f"{name} is outside target markets"
    return CriterionScore(
        criterion="geographic_fit", points=_pts(share, w), max_points=w, reasons=[reason]
    )


def _signals(f: LeadFacts, p: Profile) -> CriterionScore:
    w = p.weights.business_signals
    t = p.type_info(f.company_type)
    share, reasons = 0.0, []
    if t and t.target_model:
        share += 0.4
        reasons.append(f"'{t.label}' matches the target business model")
    if f.business_signals:
        share += 0.2 * len(f.business_signals)
        reasons.append("signals: " + ", ".join(f.business_signals))
    if f.negative_signals and not f.business_signals:
        share -= 0.33
        reasons.append("negative signals: " + ", ".join(f.negative_signals))
    if not reasons:
        reasons.append("no relevant signals found")
    return CriterionScore(
        criterion="business_signals", points=_pts(share, w), max_points=w, reasons=reasons
    )


def _offering(f: LeadFacts, p: Profile) -> CriterionScore:
    w = p.weights.offering
    core = [c for c in f.categories if (cat := p.category(c)) and cat.core]
    adjacent = [c for c in f.categories if (cat := p.category(c)) and not cat.core]
    share = 0.4 * len(core) + 0.2 * len(adjacent)
    reasons = []
    if core:
        reasons.append("core offerings: " + ", ".join(core))
    if adjacent:
        reasons.append("adjacent offerings: " + ", ".join(adjacent))
    if not reasons:
        reasons.append("no relevant offerings detected")
    return CriterionScore(
        criterion="offering_relevance", points=_pts(share, w), max_points=w, reasons=reasons
    )


def _completeness(f: LeadFacts, p: Profile) -> tuple[CriterionScore, int]:
    checks = {
        "website": (2, bool(f.website)),
        "website verified": (2, f.website_verified),
        "summary": (2, bool(f.summary)),
        "country": (1, bool(f.country)),
        "offerings detected": (2, bool(f.categories)),
        "source URL": (1, f.has_source_url),
    }
    raw = sum(pts for pts, ok in checks.values() if ok)  # out of 10
    present = [k for k, (_, ok) in checks.items() if ok]
    missing = [k for k, (_, ok) in checks.items() if not ok]
    reasons = []
    if present:
        reasons.append("available: " + ", ".join(present))
    if missing:
        reasons.append("missing: " + ", ".join(missing))
    w = p.weights.completeness
    return (
        CriterionScore(
            criterion="data_completeness", points=_pts(raw / 10, w), max_points=w, reasons=reasons
        ),
        raw,
    )


def qualification_gates(f: LeadFacts, p: Profile) -> list[str]:
    """Hard requirements for automatic qualification. A failed gate caps the
    status at needs_review: a human decides, regardless of the score."""
    gates = []
    if p.gates.require_verified_website and not f.website_verified:
        gates.append("no verified official website")
    in_market = f.country in p.primary_countries() | p.secondary_countries()
    if p.gates.require_target_market and f.country and not in_market:
        gates.append("outside target markets")
    return gates


def disqualifiers(f: LeadFacts, p: Profile) -> list[str]:
    rules = {fold(d) for d in p.signals.disqualify}
    return [s for s in f.negative_signals if fold(s) in rules]


def classify(
    score: int,
    completeness: int,
    profile: Profile,
    gates: list[str] | None = None,
    disqualified_by: list[str] | None = None,
) -> QualificationStatus:
    if disqualified_by:
        return QualificationStatus.DISQUALIFIED
    if completeness < 4:
        return QualificationStatus.INSUFFICIENT_DATA
    if score >= profile.thresholds.qualified:
        return QualificationStatus.NEEDS_REVIEW if gates else QualificationStatus.QUALIFIED
    if score >= profile.thresholds.review:
        return QualificationStatus.NEEDS_REVIEW
    return QualificationStatus.DISQUALIFIED


def score_lead(f: LeadFacts, profile: Profile) -> RuleResult:
    completeness_item, completeness = _completeness(f, profile)
    breakdown = [
        _industry(f, profile),
        _business_type(f, profile),
        _geography(f, profile),
        _signals(f, profile),
        _offering(f, profile),
        completeness_item,
    ]
    score = sum(c.points for c in breakdown)
    enriched_ok = f.enrichment_status in (EnrichmentStatus.SUCCESS, EnrichmentStatus.PARTIAL)
    confidence = round(min(1.0, 0.25 + 0.55 * completeness / 10 + (0.2 if enriched_ok else 0)), 2)
    gates = qualification_gates(f, profile)
    ruled_out = disqualifiers(f, profile)
    status = classify(score, completeness, profile, gates, ruled_out)

    ranked = [c for c in breakdown if c.max_points]
    strongest = sorted(ranked, key=lambda c: c.points / c.max_points, reverse=True)
    top = [c for c in strongest if c.points / c.max_points >= 0.6][:2]
    weak = [c for c in strongest[::-1] if c.points / c.max_points < 0.4][:2]
    parts = [f"Rule-based score {score}/100 ({status.value}) for profile '{profile.label}'."]
    if top:
        parts.append("Strengths: " + "; ".join(c.reasons[0] for c in top) + ".")
    if weak:
        parts.append("Gaps: " + "; ".join(c.reasons[-1] for c in weak) + ".")
    if ruled_out:
        parts.append(
            "Disqualified: the website says " + ", ".join(f"'{d}'" for d in ruled_out) + "."
        )
    elif gates and score >= profile.thresholds.qualified:
        parts.append("Held for human review: " + "; ".join(gates) + ".")
    return RuleResult(
        score, breakdown, confidence, status, " ".join(parts), completeness, gates, ruled_out
    )


class ScoringService:
    def __init__(self, db: Session, settings: Settings, llm: LLMProvider | None = None):
        self.db = db
        self.settings = settings
        self.llm = llm

    @staticmethod
    def profile_for(company: Company) -> Profile:
        try:
            return get_profile(company.profile)
        except ProfileError:
            log.warning("profile_missing_using_default", company_id=company.id)
            return get_profile(None)

    def _llm_review(
        self, facts: LeadFacts, rule: RuleResult, profile: Profile
    ) -> tuple[int, dict | None]:
        assert self.llm is not None
        payload = json.dumps(
            {
                "ideal_customer_profile": {
                    "label": profile.label,
                    "description": profile.description,
                },
                "facts": asdict(facts),
                "rule_result": {
                    "rule_score": rule.score,
                    "status": rule.status.value,
                    "breakdown": [c.model_dump() for c in rule.breakdown],
                },
            },
            ensure_ascii=False,
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
        profile = self.profile_for(company)
        facts = LeadFacts.from_company(company)
        rule = score_lead(facts, profile)
        adjustment, analysis = 0, None
        if (
            use_llm
            and self.llm is not None
            and rule.status != QualificationStatus.INSUFFICIENT_DATA
        ):
            adjustment, analysis = self._llm_review(facts, rule, profile)
        final = max(0, min(100, rule.score + adjustment))
        status = classify(final, rule.completeness, profile, rule.gates, rule.disqualified_by)
        reasoning = rule.reasoning
        if analysis and "error" not in analysis:
            reasoning = f"Final score {final}/100 ({status.value}). " + reasoning
        if rule.gates and final >= profile.thresholds.qualified > rule.score:
            reasoning += " Held for human review: " + "; ".join(rule.gates) + "."
        if analysis and analysis.get("reasoning"):
            reasoning += f" LLM review ({adjustment:+d}): {analysis['reasoning']}"

        record = LeadScore(
            company_id=company.id,
            profile=profile.name,
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
