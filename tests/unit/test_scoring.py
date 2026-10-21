import json

import pytest

from app.db.models import QualificationStatus
from app.domain.profiles import get_profile
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


def test_strong_lead_is_qualified_with_full_breakdown(agri):
    result = score_lead(STRONG, agri)
    assert result.status == QualificationStatus.QUALIFIED
    assert result.score == sum(c.points for c in result.breakdown)
    assert sum(c.max_points for c in result.breakdown) == 100
    assert [c.criterion for c in result.breakdown] == [
        "industry_relevance",
        "business_type_fit",
        "geographic_fit",
        "business_signals",
        "offering_relevance",
        "data_completeness",
    ]
    assert all(c.reasons for c in result.breakdown)
    assert result.confidence >= 0.9
    assert "Strengths" in result.reasoning


def test_scoring_is_deterministic(agri):
    assert score_lead(STRONG, agri) == score_lead(STRONG, agri)


def test_weights_come_from_the_profile():
    restaurants = get_profile("restaurants")
    facts = LeadFacts(
        name="To Limani",
        industry="restaurant",
        company_type="independent",
        country="GR",
        city="Piraeus",
        website="https://x.example",
        website_verified=True,
        summary="s",
        categories=["Seafood"],
        business_signals=["reservations"],
        has_source_url=True,
    )
    result = score_lead(facts, restaurants)
    maxes = {c.criterion: c.max_points for c in result.breakdown}
    assert maxes["geographic_fit"] == 20 and maxes["business_type_fit"] == 15
    assert _criteria(result)["geographic_fit"].reasons == ["Piraeus is a target city"]


def test_city_outside_focus_cities_gets_partial_geography():
    facts = LeadFacts(
        name="X",
        industry="restaurant",
        company_type="independent",
        country="GR",
        city="Larissa",
        website=None,
    )
    geo = _criteria(score_lead(facts, get_profile("restaurants")))["geographic_fit"]
    assert geo.points == 12 and "not a focus city" in geo.reasons[0]


def test_unverified_website_caps_status_at_review(agri):
    facts = LeadFacts(**{**STRONG.__dict__, "website_verified": False})
    result = score_lead(facts, agri)
    assert result.score >= agri.thresholds.qualified
    assert result.status == QualificationStatus.NEEDS_REVIEW
    assert "no verified official website" in result.gates


def test_outside_target_market_is_held_for_review(agri):
    facts = LeadFacts(**{**STRONG.__dict__, "country": "US"})
    result = score_lead(facts, agri)
    assert _criteria(result)["geographic_fit"].points == 3
    assert result.status != QualificationStatus.QUALIFIED


def test_negative_signals_are_penalised(agri):
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
        negative_signals=["add to cart"],
        has_source_url=True,
    )
    result = score_lead(facts, agri)
    assert _criteria(result)["business_signals"].points == 0
    assert result.status == QualificationStatus.DISQUALIFIED


def test_disqualifying_phrase_rules_a_lead_out():
    facts = LeadFacts(
        name="Old Taverna",
        industry="restaurant",
        company_type="independent",
        country="GR",
        city="Athens",
        website="https://x.example",
        website_verified=True,
        summary="s",
        categories=["Greek cuisine"],
        business_signals=["reservations", "menu_online"],
        negative_signals=["permanently closed"],
        has_source_url=True,
    )
    result = score_lead(facts, get_profile("restaurants"))
    assert result.score >= 65  # would otherwise qualify
    assert result.status == QualificationStatus.DISQUALIFIED
    assert "permanently closed" in result.reasoning


def test_empty_lead_has_insufficient_data(agri):
    facts = LeadFacts(
        name="Unknown Co", industry="unknown", company_type="unknown", country=None, website=None
    )
    result = score_lead(facts, agri)
    assert result.status == QualificationStatus.INSUFFICIENT_DATA
    assert result.confidence < 0.5


def test_offering_evidence_partially_substitutes_unknown_industry(agri):
    facts = LeadFacts(**{**STRONG.__dict__, "industry": "unknown"})
    assert _criteria(score_lead(facts, agri))["industry_relevance"].points == 15


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
            profile="agrifood_b2b",
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
    assert record.profile == "agrifood_b2b"
    assert "LLM review (+10)" in record.reasoning
    sent = llm.calls[0]
    assert sent["facts"]["name"] == "Corner Deli Athens"
    assert sent["ideal_customer_profile"]["label"].startswith("Agri-food")
    assert db.query(UsageEvent).one().prompt_tokens == 100


def test_llm_failure_falls_back_to_rules(db, settings):
    from app.services.scoring import ScoringService

    company = _company(db)
    record = ScoringService(db, settings, FixedLLM(fail=True)).score(company, use_llm=True)
    assert record.llm_adjustment == 0
    assert record.score == record.rule_score
    assert record.llm_analysis == {"error": "boom"}


@pytest.mark.parametrize("profile", ["restaurants", "hair_salons", "agrifood_b2b"])
def test_every_bundled_profile_can_score_an_empty_lead(profile):
    facts = LeadFacts(
        name="X", industry="unknown", company_type="unknown", country=None, website=None
    )
    assert score_lead(facts, get_profile(profile)).score == 0
