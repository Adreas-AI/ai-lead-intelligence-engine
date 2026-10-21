"""Free-text search interpretation (catalog, name matching, LLM)."""

import pytest

from app.providers.discovery.base import DiscoveryError
from app.providers.llm import LLMError, LLMProvider, LLMResult
from app.schemas import SearchRequest
from app.services.search import SearchService, slugify, split_location


class FakeLLM(LLMProvider):
    name, model = "openai", "gpt-test"

    def __init__(self, data=None, fail=False):
        self.data, self.fail = data, fail

    def complete_json(self, system, user, *, operation):
        assert operation == "search_interpretation"
        if self.fail:
            raise LLMError("down")
        return LLMResult(self.data, self.model, 50, 50)


@pytest.mark.parametrize(
    ("query", "what", "city"),
    [
        ("κρεοπωλεία στην Αθήνα", "κρεοπωλεία", "Αθήνα"),
        ("dental clinics in Thessaloniki", "dental clinics", "Thessaloniki"),
        ("κομμωτήρια σε Πάτρα", "κομμωτήρια", "Πάτρα"),
        ("vegan restaurants", "vegan restaurants", None),
    ],
)
def test_split_location(query, what, city):
    assert split_location(query) == (what, city)


def test_slugify_transliterates_greek():
    assert slugify("Κρεοπωλεία Αθήνα") == "kreopoleia_athina"


def test_catalog_interpretation_is_explicit(db, settings):
    plan, profile = SearchService(db, settings).interpret(
        SearchRequest(query="κρεοπωλεία στην Αθήνα")
    )
    assert plan.method == "catalog"
    assert plan.osm_tags == ["shop=butcher"]
    assert plan.city == "Αθήνα" and plan.country == "GR"
    assert "Butcher shops (Κρεοπωλεία)" in plan.explanation
    assert "Αθήνα (within 5 km), Greece" in plan.explanation
    assert plan.sources == ["openstreetmap"]
    assert profile.industries["butcher"].osm_tags == ["shop=butcher"]
    assert profile.geography.cities == ["Αθήνα"]
    # preview has no side effects
    assert not (settings.profiles_dir / f"{plan.profile_name}.yaml").exists()


def test_unknown_terms_fall_back_to_a_labelled_name_search(db, settings):
    plan, profile = SearchService(db, settings).interpret(
        SearchRequest(query="αγροτικά μηχανήματα", city="Λάρισα")
    )
    assert plan.method == "name_match"
    assert plan.osm_tags == [] and plan.name_keywords == ["αγροτι", "μηχανημα"]
    assert "NAME contains" in plan.explanation
    assert plan.warnings and "OPENAI_API_KEY" in plan.warnings[0]
    assert profile.discovery.osm_name_patterns == ["αγροτι", "μηχανημα"]


def test_meaningless_query_is_rejected(db, settings):
    with pytest.raises(DiscoveryError):
        SearchService(db, settings).interpret(SearchRequest(query="?? !!"))


def test_llm_interpretation_is_validated(db, settings):
    llm = FakeLLM(
        {
            "label": "Agricultural machinery dealers",
            "local_label": "Αγροτικά μηχανήματα",
            "categories": [
                {
                    "label": "Agricultural machinery dealers",
                    "local_label": "Αγροτικά μηχανήματα",
                    "osm_tags": ["shop=agrarian", "bogus", "building=yes"],
                    "synonyms": ["tractor dealer", "αγροτικά μηχανήματα"],
                }
            ],
            "name_keywords": ["αγροτικ"],
            "search_terms": ["αγροτικά μηχανήματα"],
            "website_keywords": ["tractors", "τρακτέρ"],
            "city": "Λάρισα",
            "notes": "",
        }
    )
    plan, profile = SearchService(db, settings, llm).interpret(
        SearchRequest(query="μαγαζιά για τρακτέρ στη Λάρισα")
    )
    assert plan.method == "llm"
    assert plan.osm_tags == ["shop=agrarian"]  # invalid / non-business tags dropped
    assert plan.city == "Λάρισα"
    assert "tractors" in profile.offering_categories[0].keywords


def test_llm_failure_falls_back_to_catalog(db, settings):
    plan, _ = SearchService(db, settings, FakeLLM(fail=True)).interpret(
        SearchRequest(query="κομμωτήρια")
    )
    assert plan.method == "catalog"
    assert "LLM interpretation failed" in plan.warnings[0]


def test_mock_llm_is_never_used_for_interpretation(db, settings):
    from app.providers.llm import MockLLMProvider

    plan, _ = SearchService(db, settings, MockLLMProvider()).interpret(
        SearchRequest(query="κομμωτήρια")
    )
    assert plan.method == "catalog" and plan.warnings == []


def test_offer_becomes_the_outreach_pitch(db, settings):
    _, profile = SearchService(db, settings).interpret(
        SearchRequest(query="bakeries", offer="a flour delivery service for bakeries")
    )
    assert profile.value_proposition() == "a flour delivery service for bakeries"
