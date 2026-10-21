import pytest

from app.domain.business_catalog import match_categories
from app.domain.geo import normalize_country
from app.domain.keywords import find_keywords, fold
from app.domain.normalize import extract_domain, name_similarity, normalize_name, normalize_url
from app.domain.profiles import Profile, ProfileError, get_profile, list_profile_names


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
        # Greek names and legal forms are kept / stripped correctly
        ("Κρεοπωλείο Παπαδόπουλος Ο.Ε.", "κρεοπωλειο παπαδοπουλος"),
        ("Αφοί Νίκα Α.Ε.", "αφοι νικα"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.Aegean-Citrus.example/", "aegean-citrus.example"),
        ("aegean-citrus.example", "aegean-citrus.example"),
        ("http://shop.example.gr/en/products?x=1", "shop.example.gr"),
        ("https://www.facebook.com/somefarm", None),
        ("https://gmail.com", None),  # webmail is not a company website
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
    assert name_similarity("Κρεοπωλείο Νίκος", "ΚΡΕΟΠΩΛΕΙΟ ΝΙΚΟΣ") == 1.0


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("GR", "GR"),
        ("gr", "GR"),
        ("Greece", "GR"),
        ("Hellas", "GR"),
        ("Ελλάδα", "GR"),
        ("UK", "GB"),
        ("Atlantis", None),
        ("", None),
    ],
)
def test_normalize_country(value, code):
    assert normalize_country(value) == code


def test_keywords_are_word_bounded_and_accent_insensitive():
    assert find_keywords("We sell dried figs and nuts", ["figs", "nuts"]) == ["figs", "nuts"]
    assert find_keywords("peanuts only", ["nuts"]) == []
    assert find_keywords("Κλείσε ΡΑΝΤΕΒΟΥ online", ["ραντεβού"]) == ["ραντεβού"]
    assert fold("Κομμωτήριο") == "κομμωτηριο"


# --- profiles ---------------------------------------------------------------------------
def test_bundled_profiles_load():
    names = list_profile_names()
    assert {"restaurants", "hair_salons", "agrifood_b2b"} <= set(names)
    for name in names:
        p = get_profile(name)
        assert sum(p.weights.model_dump().values()) == 100


@pytest.mark.parametrize(
    ("profile", "text", "slug"),
    [
        ("agrifood_b2b", "Food wholesale", "food_wholesale"),
        ("agrifood_b2b", "Import/Export", "import_export"),
        ("agrifood_b2b", "food_distribution", "food_distribution"),
        ("agrifood_b2b", "Software", "other"),
        ("agrifood_b2b", "", "unknown"),
        ("hair_salons", "Κομμωτήριο", "hair_salon"),
        ("hair_salons", "Barber Shop", "barbershop"),
        ("restaurants", "ταβέρνα", "restaurant"),
        ("restaurants", "Cafe", "cafe"),
    ],
)
def test_profile_industry_classification(profile, text, slug):
    assert get_profile(profile).classify_industry(text) == slug


def test_profile_business_type_classification(agri):
    assert agri.classify_business_type("Wholesaler") == "wholesaler"
    assert agri.classify_business_type("software agency") == "service_provider"
    assert agri.classify_business_type(None) == "unknown"
    assert agri.infer_business_type("we export in bulk") == "exporter"
    # non-target types are never inferred from website text
    assert agri.infer_business_type("visit our shop") is None


def _minimal(**overrides):
    data = {
        "name": "x",
        "label": "X",
        "description": "d",
        "industries": {"a": {"label": "A", "fit": 1}},
        "business_types": {"t": {"label": "T", "fit": 1}},
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"weights": {"industry": 50}}, "sum to 100"),
        ({"thresholds": {"qualified": 40, "review": 60}}, "review must be"),
        ({"industries": {"unknown": {"label": "U", "fit": 1}}}, "reserved"),
        ({"geography": {"primary_countries": ["Narnia"]}}, "unknown country"),
        ({"industries": {"a": {"label": "A", "fit": 1, "osm_tags": ["shop"]}}}, "key=value"),
        ({"outreach": {"relevance_by_type": {"nope": "x"}}}, "unknown type"),
        ({"surprise": 1}, "Extra inputs"),
    ],
)
def test_invalid_profiles_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        Profile.model_validate(_minimal(**overrides))


def test_unquoted_yaml_no_is_norway():
    p = Profile.model_validate(_minimal(geography={"primary_countries": [False, "GR"]}))
    assert p.geography.primary_countries == ["NO", "GR"]


def test_unknown_profile():
    with pytest.raises(ProfileError, match="unknown profile"):
        get_profile("does_not_exist")
    with pytest.raises(ProfileError, match="invalid profile name"):
        get_profile("../etc/passwd")


# --- free-text catalog ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("query", "keys"),
    [
        ("κρεοπωλεία", ["butcher"]),
        ("Κρεοπωλείο", ["butcher"]),
        ("butcher shops", ["butcher"]),
        ("οδοντιατρεία στην Αθήνα", ["dentist"]),
        ("dental clinics", ["dentist"]),  # not also the generic "clinic"
        ("φούρνοι και ζαχαροπλαστεία", ["pastry", "bakery"]),
        ("hair salons and barbershops", ["hairdresser", "barber"]),
        ("barber", ["barber"]),  # not "bar"
        ("συνεργεία αυτοκινήτων", ["car_repair"]),
        ("αγροτικά μηχανήματα", []),
    ],
)
def test_catalog_understands_greek_and_english(query, keys):
    assert [c.key for c in match_categories(query)] == keys
