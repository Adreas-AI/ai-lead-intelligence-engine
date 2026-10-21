import pytest

from app.domain.normalize import extract_domain, name_similarity, normalize_name, normalize_url
from app.domain.taxonomy import (
    classify_company_type,
    classify_industry,
    find_keywords,
    normalize_country,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Olympia Fresh Produce S.A.", "olympia fresh produce"),
        ("KALAMATA VALLEY OLIVE OIL SA", "kalamata valley olive oil"),
        ("Andalucía Fresh Produce S.L.", "andalucia fresh produce"),
        ("Berlin Organic Wholesale GmbH", "berlin organic wholesale"),
        ("Sicilia Pistachio & Nut Co. S.r.l.", "sicilia pistachio nut"),
        ("Aegean Foods I.K.E.", "aegean foods"),
        ("The Mart", "mart"),
    ],
)
def test_normalize_name_strips_legal_forms(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.Aegean-Citrus.example/", "aegean-citrus.example"),
        ("aegean-citrus.example", "aegean-citrus.example"),
        ("http://shop.example.gr/en/products?x=1", "shop.example.gr"),
        ("https://www.facebook.com/somefarm", None),
        ("not a url", None),
        (None, None),
    ],
)
def test_extract_domain(url, domain):
    assert extract_domain(url) == domain


def test_normalize_url_adds_scheme_and_strips_trailing_slash():
    assert normalize_url("Example.COM/about/") == "https://example.com/about"


def test_name_similarity():
    assert name_similarity("Thessaly Grain Traders", "Thessaly Grain Trader") > 0.9
    assert name_similarity("Thessaly Grain Traders", "Crete Avocado Growers") < 0.5
    assert name_similarity("Fresh S.A.", "fresh sa") == 1.0


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Food wholesale", "food_wholesale"),
        ("Import/Export", "import_export"),
        ("Agriculture", "agriculture"),
        ("food processing", "food_manufacturing"),
        ("Software", "other"),
        ("", "unknown"),
        (None, "unknown"),
        ("food_distribution", "food_distribution"),
    ],
)
def test_classify_industry(text, slug):
    assert classify_industry(text) == slug


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Wholesaler", "wholesaler"),
        ("Cooperative", "cooperative"),
        ("Service provider", "service_provider"),
        ("software agency", "service_provider"),
        ("Food manufacturer", "food_manufacturer"),
        (None, "unknown"),
    ],
)
def test_classify_company_type(text, slug):
    assert classify_company_type(text) == slug


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("GR", "GR"),
        ("gr", "GR"),
        ("Greece", "GR"),
        ("Hellas", "GR"),
        ("UK", "GB"),
        ("Atlantis", None),
        ("", None),
    ],
)
def test_normalize_country(value, code):
    assert normalize_country(value) == code


def test_find_keywords_is_word_bounded():
    assert find_keywords("We sell dried figs and nuts", ["figs", "nuts"]) == ["figs", "nuts"]
    assert find_keywords("peanuts only", ["nuts"]) == []
