from app.services.extraction import (
    detect_country,
    extract_profile,
    extractive_summary,
    name_matches_site,
    parse_page,
)

HTML = """<!doctype html><html lang="en"><head>
<title>Aegean Citrus Growers Cooperative | Citrus from Greece</title>
<meta name="description" content="Cooperative of citrus growers supplying oranges to wholesalers.">
<meta property="og:site_name" content="Aegean Citrus Growers Cooperative">
<link rel="alternate" hreflang="el" href="/el/">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization",
"name":"Aegean Citrus Growers Cooperative","address":{"@type":"PostalAddress",
"addressLocality":"Argos","addressCountry":"Greece"}}]}</script>
<script>var tracking = "add to cart";</script>
</head><body>
<nav><a href="/about-us">About</a><a href="https://other.example/about">External</a></nav>
<p>We grow oranges and mandarins and ship by pallet to importers in Germany.
GLOBALG.A.P certified. Trade enquiries welcome.</p>
</body></html>"""


def test_parse_page_reads_meta_jsonld_and_links():
    facts = parse_page(HTML, "https://aegean-citrus.example/")
    assert facts.title.startswith("Aegean Citrus Growers Cooperative")
    assert facts.meta_description.startswith("Cooperative of citrus growers")
    assert facts.address_country == "Greece"
    assert facts.address_locality == "Argos"
    assert facts.lang == "en"
    assert facts.about_links == ["https://aegean-citrus.example/about-us"]
    assert "tracking" not in facts.text  # scripts are stripped


def test_extract_profile_detects_categories_signals_and_markets():
    facts = parse_page(HTML, "https://aegean-citrus.example/")
    profile = extract_profile(facts.text)
    assert profile.categories == ["Fresh fruit"]
    assert {"oranges", "mandarins"} <= set(profile.products)
    assert {"bulk_supply", "import", "b2b_explicit"} <= set(profile.business_signals)
    assert "Germany" in profile.markets_mentioned
    assert profile.b2c_signals == []


def test_extract_profile_on_empty_text_returns_nothing():
    profile = extract_profile("")
    assert profile.categories == [] and profile.business_signals == []
    assert profile.inferred_company_type is None


def test_detect_country_prefers_structured_data_then_cctld():
    facts = parse_page(HTML, "https://aegean-citrus.example/")
    assert detect_country(facts, "https://aegean-citrus.example/") == (
        "GR",
        "schema.org address: Greece",
    )
    bare = parse_page("<html><body>hello</body></html>", "https://farm.gr")
    code, evidence = detect_country(bare, "https://farm.gr")
    assert code == "GR" and "inferred" in evidence
    assert detect_country(bare, "https://farm.com") == (None, None)


def test_name_matches_site():
    facts = parse_page(HTML, "https://aegean-citrus.example/")
    assert name_matches_site("Aegean Citrus Growers Cooperative", facts)
    assert not name_matches_site("Olympus Agro Supplies", facts)


def test_extractive_summary_uses_company_words_only():
    facts = parse_page(HTML, "https://aegean-citrus.example/")
    assert extractive_summary(facts) == facts.meta_description
    assert (
        extractive_summary(parse_page("<html><body>Hi</body></html>", "https://x.example")) is None
    )
