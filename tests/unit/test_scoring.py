import json

from app.db.models import QualificationStatus
from app.providers.llm import LLMError, LLMProvider, LLMResult
from app.services.scoring import LeadFacts, score_lead

STRONG = LeadFacts(
    name="Aegean Citrus Growers Cooperative",
    industry="agriculture",
    company_type="cooperative",
    country="GR",
    website="https://aegean-citrus.example",
    website_verified=True,
    enrichment_status="success",
    summary="Cooperative of citrus growers.",
    categories=["Fresh fruit"],
    products=["oranges"],
    business_signals=["export", "bulk_supply"],
    has_source_url=True,
)


def _criteria(result):
    return {c.criterion: c for c in result.breakdown}


def test_strong_lead_is_qualified_with_full_breakdown(settings):
    result = score_lead(STRONG, settings)
    assert result.status == QualificationStatus.QUALIFIED
    assert result.score == sum(c.points for c in result.breakdown)
    assert sum(c.max_points for c in result.breakdown) == 100
    assert all(c.reasons for c in result.breakdown)
    assert result.confidence >= 0.9
    assert "Strengths" in result.reasoning


def test_scoring_is_deterministic(settings):
    assert score_lead(STRONG, settings) == score_lead(STRONG, settings)


def test_unverified_website_caps_status_at_review(settings):
    facts = LeadFacts(**{**STRONG.__dict__, "website_verified": False})
    result = score_lead(facts, settings)
    assert result.score >= settings.qualification_threshold
    assert result.status == QualificationStatus.NEEDS_REVIEW
    assert "no verified official website" in result.gates


def test_outside_target_market_is_held_for_review(settings):
    facts = LeadFacts(**{**STRONG.__dict__, "country": "US"})
    result = score_lead(facts, settings)
    assert _criteria(result)["geographic_fit"].points == 3
    assert result.status != QualificationStatus.QUALIFIED


def test_consumer_only_business_is_penalised(settings):
    facts = LeadFacts(
        name="Corner Deli",
        industry="food_retail",
        company_type="retailer",
        country="GR",
        website="https://corner-deli.example",
        website_verified=True,
        enrichment_status="success",
        summary="Deli",
        categories=["Olive oil & olives"],
        b2c_signals=["add to cart"],
        has_source_url=True,
    )
    result = score_lead(facts, settings)
    assert _criteria(result)["b2b_business_model"].points == 0
    assert result.status == QualificationStatus.DISQUALIFIED


def test_empty_lead_has_insufficient_data(settings):
    facts = LeadFacts(
        name="Unknown Co", industry="unknown", company_type="unknown", country=None, website=None
    )
    result = score_lead(facts, settings)
    assert result.status == QualificationStatus.INSUFFICIENT_DATA
    assert result.confidence < 0.5


def test_product_evidence_partially_substitutes_unknown_industry(settings):
    facts = LeadFacts(**{**STRONG.__dict__, "industry": "unknown"})
    assert _criteria(score_lead(facts, settings))["industry_relevance"].points == 15


class FixedLLM(LLMProvider):
    name, model = "fixed", "fixed-1"

    def __init__(self, data=None, fail=False):
        self.data, self.fail, self.calls = data, fail, []

    def complete_json(self, system, user, *, operation):
        self.calls.append(json.loads(user))
        if self.fail:
            raise LLMError("boom")
        return LLMResult(self.data, self.model, 100, 20)


def _company(db):
    from app.schemas import CompanyIn
    from app.services.leads import create_company

    return create_company(
        db,
        CompanyIn(
            name="Corner Deli Athens",
            country="GR",
            website="https://corner-deli.example",
            source_url="https://example.org/source",
            industry="Food retail",
            company_type="Retailer",
            description="A deli.",
        ),
    )


def test_llm_adjustment_is_clamped_and_recorded(db, settings):
    from app.db.models import UsageEvent
    from app.services.scoring import ScoringService

    company = _company(db)
    llm = FixedLLM({"adjustment": 50, "reasoning": "Looks great", "risks": ["x"]})
    record = ScoringService(db, settings, llm).score(company, use_llm=True)
    assert record.llm_adjustment == 10
    assert record.score == record.rule_score + 10
    assert "LLM review (+10)" in record.reasoning
    assert llm.calls[0]["facts"]["name"] == "Corner Deli Athens"
    assert db.query(UsageEvent).one().prompt_tokens == 100


def test_llm_failure_falls_back_to_rules(db, settings):
    from app.services.scoring import ScoringService

    company = _company(db)
    record = ScoringService(db, settings, FixedLLM(fail=True)).score(company, use_llm=True)
    assert record.llm_adjustment == 0
    assert record.score == record.rule_score
    assert record.llm_analysis == {"error": "boom"}
