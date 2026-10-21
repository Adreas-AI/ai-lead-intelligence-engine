"""External providers, tested against mocked HTTP (respx) and fake clients: no network."""

from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core.http import HttpClient
from app.core.retry import RetryPolicy
from app.providers.crm import CrmError, HubSpotProvider
from app.providers.discovery.google_places import GooglePlacesDiscoveryProvider
from app.providers.discovery.mock import MockDiscoveryProvider
from app.providers.discovery.wikidata import WikidataDiscoveryProvider, build_query
from app.providers.llm import LLMError, OpenAIProvider
from app.providers.web import FetchBlocked, HttpPageFetcher
from app.schemas import DiscoveryRequest


@pytest.fixture()
def fast_http(settings):
    return HttpClient(
        settings,
        rate_per_second=1000,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0, jitter=False),
    )


# --- discovery ---------------------------------------------------------------------
def test_mock_discovery_filters(settings):
    provider = MockDiscoveryProvider(settings)
    everything = provider.search(DiscoveryRequest(limit=50)).companies
    assert len(everything) == 7 and all(c.is_synthetic for c in everything)
    greek = provider.search(DiscoveryRequest(countries=["GR"])).companies
    assert [c.name for c in greek] == ["Crete Avocado Growers"]
    traders = provider.search(DiscoveryRequest(company_types=["trader"])).companies
    assert [c.name for c in traders] == ["Szeged Paprika Traders Kft."]
    assert provider.search(DiscoveryRequest(keywords=["paprika"])).companies[0].country == "HU"


def test_wikidata_query_contains_mapped_ids():
    q = build_query(["Q540912"], ["Q41"], 10)
    assert "wd:Q540912" in q and "wd:Q41" in q and "LIMIT 10" in q


@respx.mock
def test_wikidata_parses_bindings(settings, fast_http):
    respx.get(settings.wikidata_sparql_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "company": {"value": "http://www.wikidata.org/entity/Q9189808"},
                            "name_en": {"value": "Chipita"},
                            "site": {"value": "http://www.chipita.com/"},
                            "ind": {"value": "http://www.wikidata.org/entity/Q540912"},
                            "ctry": {"value": "http://www.wikidata.org/entity/Q41"},
                        },
                        {
                            "company": {"value": "http://www.wikidata.org/entity/Q1"}
                        },  # no name: skipped
                    ]
                }
            },
        )
    )
    out = WikidataDiscoveryProvider(settings, http=fast_http).search(
        DiscoveryRequest(provider="wikidata", countries=["GR"], industries=["food_manufacturing"])
    )
    assert len(out.companies) == 1
    c = out.companies[0]
    assert (c.name, c.country, c.industry, c.source_ref) == (
        "Chipita",
        "GR",
        "food industry",
        "Q9189808",
    )
    assert c.website == "http://www.chipita.com"  # scheme is preserved


def test_google_places_requires_key(settings):
    from app.providers.discovery.base import ProviderNotConfigured

    with pytest.raises(ProviderNotConfigured):
        GooglePlacesDiscoveryProvider(settings).search(DiscoveryRequest(provider="google_places"))


@respx.mock
def test_google_places_maps_results_and_counts_cost(settings, fast_http):
    from pydantic import SecretStr

    s = settings.model_copy(update={"google_places_api_key": SecretStr("test-key")})
    route = respx.post(s.google_places_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "places": [
                    {
                        "id": "abc",
                        "displayName": {"text": "Olive Exporters"},
                        "websiteUri": "https://olive-exporters.example",
                        "googleMapsUri": "https://maps/abc",
                        "types": ["wholesaler", "point_of_interest"],
                        "businessStatus": "OPERATIONAL",
                        "addressComponents": [
                            {"longText": "Kalamata", "types": ["locality"]},
                            {"shortText": "GR", "types": ["country"]},
                        ],
                    },
                    {
                        "id": "closed",
                        "displayName": {"text": "Gone"},
                        "businessStatus": "CLOSED_PERMANENTLY",
                    },
                ]
            },
        )
    )
    out = GooglePlacesDiscoveryProvider(s, http=fast_http).search(
        DiscoveryRequest(
            provider="google_places", countries=["GR"], keywords=["olive oil"], limit=5
        )
    )
    assert route.calls[0].request.headers["X-Goog-Api-Key"] == "test-key"
    assert [c.name for c in out.companies] == ["Olive Exporters"]
    assert out.companies[0].city == "Kalamata" and out.companies[0].keywords == ["wholesaler"]
    assert out.requests_made == 1 and out.cost_usd == s.google_places_cost_per_request


# --- website fetching -----------------------------------------------------------------
@respx.mock
def test_fetcher_respects_robots_disallow(settings, fast_http):
    respx.get("https://farm.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /")
    )
    with pytest.raises(FetchBlocked, match="robots.txt disallows"):
        HttpPageFetcher(settings, http=fast_http).fetch("https://farm.test/")


@respx.mock
def test_fetcher_does_not_bypass_anti_bot_pages(settings, fast_http):
    respx.get("https://farm.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://farm.test/").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><title>Attention Required! | Cloudflare</title></html>",
        )
    )
    with pytest.raises(FetchBlocked, match="anti-bot"):
        HttpPageFetcher(settings, http=fast_http).fetch("https://farm.test/")


@respx.mock
def test_fetcher_treats_403_as_blocked_and_5xx_robots_as_disallowed(settings, fast_http):
    respx.get("https://a.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://a.test/").mock(return_value=httpx.Response(403))
    with pytest.raises(FetchBlocked, match="403"):
        HttpPageFetcher(settings, http=fast_http).fetch("https://a.test/")
    respx.get("https://b.test/robots.txt").mock(return_value=httpx.Response(503))
    with pytest.raises(FetchBlocked, match="503"):
        HttpPageFetcher(settings, http=fast_http).fetch("https://b.test/")


@respx.mock
def test_fetcher_returns_html_and_sends_user_agent(settings, fast_http):
    respx.get("https://farm.test/robots.txt").mock(return_value=httpx.Response(200, text=""))
    page = respx.get("https://farm.test/").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text="<html>ok</html>"
        )
    )
    result = HttpPageFetcher(settings, http=fast_http).fetch("https://farm.test/")
    assert result.html == "<html>ok</html>"
    assert page.calls[0].request.headers["User-Agent"] == settings.http_user_agent


# --- LLM ------------------------------------------------------------------------------
class FakeCompletions:
    def __init__(self, content, fail_times=0):
        self.content, self.fail_times, self.calls = content, fail_times, 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise httpx.ConnectError("transient")
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(
            model="gpt-test",
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500),
        )


def _openai(settings, completions):
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = OpenAIProvider(settings, client=client)
    provider.retry_policy = RetryPolicy(max_attempts=3, base_delay=0, jitter=False)
    return provider


def test_openai_provider_parses_json_and_costs(settings):
    provider = _openai(settings, FakeCompletions('{"adjustment": 2}', fail_times=1))
    result = provider.complete_json("sys", "{}", operation="lead_scoring")
    assert result.data == {"adjustment": 2}
    assert provider.cost_usd(result) == pytest.approx(
        1000 / 1e6 * settings.openai_input_cost_per_1m
        + 500 / 1e6 * settings.openai_output_cost_per_1m
    )


def test_openai_provider_rejects_invalid_json(settings):
    with pytest.raises(LLMError, match="invalid JSON"):
        _openai(settings, FakeCompletions("not json")).complete_json("s", "{}", operation="x")


def test_openai_provider_requires_key(settings):
    from app.providers.llm import LLMConfigurationError

    with pytest.raises(LLMConfigurationError):
        OpenAIProvider(settings)


# --- HubSpot --------------------------------------------------------------------------
@respx.mock
def test_hubspot_search_create_update(settings, fast_http):
    from pydantic import SecretStr

    s = settings.model_copy(update={"hubspot_access_token": SecretStr("pat-test")})
    base = s.hubspot_base_url
    search = respx.post(f"{base}/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "42"}]})
    )
    create = respx.post(f"{base}/crm/v3/objects/companies").mock(
        return_value=httpx.Response(201, json={"id": "43"})
    )
    update = respx.patch(f"{base}/crm/v3/objects/companies/42").mock(
        return_value=httpx.Response(200, json={"id": "42"})
    )
    hub = HubSpotProvider(s, http=fast_http)
    assert hub.find_by_domain("a.example") == "42"
    assert hub.create_company({"name": "A"}) == "43"
    hub.update_company("42", {"name": "A"})
    assert search.calls[0].request.headers["Authorization"] == "Bearer pat-test"
    assert create.called and update.called


@respx.mock
def test_hubspot_errors_become_crm_errors(settings, fast_http):
    from pydantic import SecretStr

    s = settings.model_copy(update={"hubspot_access_token": SecretStr("pat-test")})
    respx.post(f"{s.hubspot_base_url}/crm/v3/objects/companies").mock(
        return_value=httpx.Response(400, json={"message": "Property values were not valid"})
    )
    with pytest.raises(CrmError, match="400"):
        HubSpotProvider(s, http=fast_http).create_company({"name": "A"})


def test_hubspot_requires_token(settings):
    with pytest.raises(CrmError):
        HubSpotProvider(settings)
