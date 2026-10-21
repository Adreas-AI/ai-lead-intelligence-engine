import pytest

from app.domain.profiles import get_profile
from app.services.outreach import DraftFacts, template_draft, validate_draft

FACTS = DraftFacts(
    name="Rotterdam Fresh Import B.V.",
    country="Netherlands",
    city="Rotterdam",
    business_type="Importer",
    categories=["Fresh fruit"],
    products=["avocados", "table grapes"],
    business_signals=["import", "distribution"],
    markets_mentioned=["Netherlands", "Belgium"],
    summary="Importer and distributor of fresh fruit for the Benelux market.",
    website="https://rotterdam-fresh-import.example",
    website_verified=True,
)


def test_template_uses_only_verified_facts_and_the_profile_pitch(settings, agri):
    subject, body = template_draft(FACTS, agri, settings, "importer")
    assert "Rotterdam Fresh Import B.V." in body
    assert "avocados" in body
    assert "source directly from producers" in body  # profile's relevance_by_type
    assert agri.value_proposition().strip() in body
    assert settings.sender_company in body
    assert validate_draft(subject, body, FACTS, agri, settings) == []


def test_template_without_categories_is_honest(settings, agri):
    facts = DraftFacts(**{**FACTS.__dict__, "categories": [], "products": []})
    _, body = template_draft(facts, agri, settings, None)
    assert "while looking at agri-food b2b suppliers & buyers in Rotterdam" in body
    assert "avocados" not in body


@pytest.mark.parametrize("profile", ["restaurants", "hair_salons"])
def test_profile_pitch_text_is_not_flagged_by_guardrails(settings, profile):
    p = get_profile(profile)
    facts = DraftFacts(
        **{
            **FACTS.__dict__,
            "name": "Lumiere",
            "categories": [],
            "products": [],
            "business_signals": list(p.outreach.signal_lines),
        }
    )
    subject, body = template_draft(facts, p, settings, None)
    assert validate_draft(subject, body, facts, p, settings) == []


def test_guardrails_reject_invented_numbers_and_claims(settings, agri):
    body = (
        "Hello Rotterdam Fresh Import B.V. team, we can increase your sales by 40% and our "
        "certified organic buyers love your award-winning avocados. " * 2
    )
    problems = validate_draft("Grow 40%", body, FACTS, agri, settings)
    joined = " ".join(problems)
    assert "numbers not present" in joined
    assert "disallowed claims" in joined
    assert "'organic'" in joined and "'certified'" in joined


def test_guardrails_reject_placeholders_and_missing_name(settings, agri):
    body = "Hello [FIRST NAME], I noticed your company. " * 5
    problems = validate_draft("Hi", body, FACTS, agri, settings)
    assert "contains placeholder text" in problems
    assert "does not mention the company name" in problems
    assert "subject length out of range" in problems
