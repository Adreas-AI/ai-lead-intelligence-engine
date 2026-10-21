from app.services.outreach import DraftFacts, template_draft, validate_draft

FACTS = DraftFacts(
    name="Rotterdam Fresh Import B.V.",
    country="Netherlands",
    company_type="importer",
    categories=["Fresh fruit"],
    products=["avocados", "table grapes"],
    business_signals=["import", "distribution"],
    markets_mentioned=["Netherlands", "Belgium"],
    summary="Importer and distributor of fresh fruit for the Benelux market.",
    website="https://rotterdam-fresh-import.example",
    website_verified=True,
)


def test_template_uses_only_verified_facts(settings):
    subject, body = template_draft(FACTS, settings)
    assert "Rotterdam Fresh Import B.V." in body
    assert "avocados" in body
    assert "source directly from producers" in body  # demand-side relevance
    assert settings.sender_company in body
    assert validate_draft(subject, body, FACTS, settings) == []


def test_template_without_categories_is_honest(settings):
    facts = DraftFacts(**{**FACTS.__dict__, "categories": [], "products": []})
    _, body = template_draft(facts, settings)
    assert "while researching agri-food companies in Netherlands" in body
    assert "avocados" not in body


def test_guardrails_reject_invented_numbers_and_claims(settings):
    body = (
        "Hello Rotterdam Fresh Import B.V. team, we can increase your sales by 40% and our "
        "certified organic buyers love your award-winning avocados. " * 2
    )
    problems = validate_draft("Grow 40%", body, FACTS, settings)
    joined = " ".join(problems)
    assert "numbers not present" in joined
    assert "disallowed claims" in joined
    assert "'organic'" in joined and "'certified'" in joined


def test_guardrails_reject_placeholders_and_missing_name(settings):
    body = "Hello [FIRST NAME], I noticed your company. " * 5
    problems = validate_draft("Hi", body, FACTS, settings)
    assert "contains placeholder text" in problems
    assert "does not mention the company name" in problems
    assert "subject length out of range" in problems
