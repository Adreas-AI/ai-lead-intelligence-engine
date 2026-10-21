"""OpenStreetMap discovery against mocked Nominatim + Overpass (no network)."""

import re

import httpx
import pytest
import respx

from app.core.http import HttpClient
from app.core.retry import RetryPolicy
from app.domain.profiles import Profile, get_profile
from app.providers.discovery.base import DiscoveryError
from app.providers.discovery.openstreetmap import OpenStreetMapDiscoveryProvider, name_regex
from app.schemas import DiscoveryRequest

ELEMENTS = [
    {
        "type": "node",
        "id": 1,
        "tags": {
            "shop": "hairdresser",
            "name": "George & Georgia",
            "website": "https://george.example",
            "addr:city": "Αθήνα",
        },
    },
    {
        "type": "way",
        "id": 2,
        "tags": {"shop": "barber", "name": "SIR Barber", "contact:website": "sirbarber.example"},
    },
    {"type": "node", "id": 3, "tags": {"shop": "hairdresser"}},  # no name: skipped
    {
        "type": "node",
        "id": 4,
        "tags": {  # same website as #1: deduplicated
            "shop": "hairdresser",
            "name": "George & Georgia II",
            "website": "https://george.example",
        },
    },
]


@pytest.fixture()
def osm(settings):
    http = HttpClient(
        settings, rate_per_second=1000, retry_policy=RetryPolicy(max_attempts=1, jitter=False)
    )
    return OpenStreetMapDiscoveryProvider(settings, http=http)


@respx.mock
def test_city_search_geocodes_and_maps_results(settings, osm):
    geo = respx.get(settings.nominatim_url).mock(
        return_value=httpx.Response(200, json=[{"lat": "37.97", "lon": "23.73"}])
    )
    overpass = respx.post(settings.overpass_endpoints[0]).mock(
        return_value=httpx.Response(200, json={"elements": ELEMENTS})
    )
    out = osm.search(
        DiscoveryRequest(provider="openstreetmap", countries=["GR"], cities=["Αθήνα"], limit=10),
        get_profile("hair_salons"),
    )
    assert geo.calls[0].request.url.params["countrycodes"] == "gr"
    ql = overpass.calls[0].request.content.decode()
    assert "around%3A5000%2C37.970000%2C23.730000" in ql  # 5 km radius around the city
    assert "shop%22%3D%22hairdresser" in ql and "shop%22%3D%22barber" in ql
    assert [c.name for c in out.companies] == ["George & Georgia", "SIR Barber"]
    first, second = out.companies
    assert first.industry == "hair_salon" and second.industry == "barbershop"
    assert first.city == "Αθήνα" and first.country == "GR"
    assert second.website == "https://sirbarber.example"
    assert first.source_url == "https://www.openstreetmap.org/node/1"
    assert first.profile == "hair_salons" and first.source == "openstreetmap"
    assert out.requests_made == 2


def test_country_query_and_website_filter(settings, osm):
    ql, tags = osm.build_query(
        DiscoveryRequest(provider="openstreetmap", countries=["GR"], require_website=True),
        get_profile("restaurants"),
    )
    assert 'area["ISO3166-1"="GR"][admin_level=2]->.c0;' in ql
    assert '["amenity"="restaurant"]' in ql and "website|contact:website" in ql
    assert tags["amenity=restaurant"] == "restaurant"
    ql_no_web, _ = osm.build_query(
        DiscoveryRequest(provider="openstreetmap", countries=["GR"], require_website=False),
        get_profile("restaurants"),
    )
    assert "website" not in ql_no_web


@respx.mock
def test_unknown_city_is_reported(settings, osm):
    respx.get(settings.nominatim_url).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(DiscoveryError, match="could not find the city"):
        osm.search(
            DiscoveryRequest(provider="openstreetmap", countries=["GR"], cities=["Atlantis"]),
            get_profile("hair_salons"),
        )


def test_profile_without_osm_tags_is_rejected(settings, osm, agri):
    with pytest.raises(DiscoveryError, match="no OpenStreetMap tags"):
        osm.build_query(
            DiscoveryRequest(
                provider="openstreetmap", countries=["GR"], industries=["agriculture"]
            ),
            agri,
        )


def test_name_patterns_are_accent_and_case_insensitive(settings, osm):
    rx = name_regex("κρεοπωλει")
    assert re.fullmatch(rx, "Κρεοπωλει") and re.fullmatch(rx, "κρεοπωλεί")
    profile = Profile.model_validate(
        {
            "name": "x",
            "label": "X",
            "description": "d",
            "industries": {"target": {"label": "T", "fit": 1}},
            "business_types": {"t": {"label": "T", "fit": 1}},
            "discovery": {"osm_name_patterns": ["κρεοπωλει"]},
        }
    )
    ql, _ = osm.build_query(DiscoveryRequest(provider="openstreetmap", countries=["GR"]), profile)
    assert '["name"~"' in ql and "shop|amenity|office" in ql


@respx.mock
def test_busy_server_falls_back_to_next_endpoint(settings, osm):
    first, second = settings.overpass_endpoints[:2]
    respx.post(first).mock(return_value=httpx.Response(502))
    backup = respx.post(second).mock(return_value=httpx.Response(200, json={"elements": ELEMENTS}))
    out = osm.search(
        DiscoveryRequest(provider="openstreetmap", countries=["GR"]), get_profile("hair_salons")
    )
    assert backup.called and len(out.companies) == 2


@respx.mock
def test_all_servers_down_gives_a_clear_message(settings, osm):
    for endpoint in settings.overpass_endpoints:
        respx.post(endpoint).mock(return_value=httpx.Response(504))
    with pytest.raises(DiscoveryError, match="busy or unreachable"):
        osm.search(
            DiscoveryRequest(provider="openstreetmap", countries=["GR"]), get_profile("hair_salons")
        )
