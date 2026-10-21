"""Google Places API (New) Text Search provider.

Requires GOOGLE_PLACES_API_KEY. Review the Google Maps Platform Terms before
storing results long-term: Places content has caching restrictions (the place
ID, stored here as ``source_ref``, may be kept indefinitely).
"""

from __future__ import annotations

from itertools import product

from app.core.config import Settings
from app.core.http import HttpClient
from app.core.logging import get_logger
from app.domain.geo import COUNTRY_NAMES
from app.domain.profiles import Profile
from app.providers.discovery.base import (
    DiscoveryOutcome,
    DiscoveryProvider,
    DiscoveryQuery,
    ProviderNotConfigured,
    selected_industries,
)
from app.schemas import CompanyIn

log = get_logger(__name__)

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.websiteUri",
        "places.formattedAddress",
        "places.addressComponents",
        "places.types",
        "places.googleMapsUri",
        "places.businessStatus",
    ]
)


class GooglePlacesDiscoveryProvider(DiscoveryProvider):
    name = "google_places"
    description = "Google Places Text Search (paid API, requires GOOGLE_PLACES_API_KEY)."
    requires_credentials = True

    def __init__(self, settings: Settings, http: HttpClient | None = None):
        self.settings = settings
        self.http = http or HttpClient(settings, rate_per_second=5)

    def is_configured(self) -> bool:
        return self.settings.google_places_api_key is not None

    @staticmethod
    def _terms(query: DiscoveryQuery, profile: Profile) -> list[tuple[str, str | None]]:
        """(search term, industry slug) pairs."""
        if query.keywords:
            return [(k, None) for k in query.keywords]
        if query.industries:
            return [(profile.industries[s].label, s) for s in selected_industries(query, profile)]
        if profile.discovery.search_terms:
            return [(t, None) for t in profile.discovery.search_terms]
        return [(i.label, s) for s, i in profile.industries.items()]

    def search(self, query: DiscoveryQuery, profile: Profile) -> DiscoveryOutcome:
        if not self.is_configured():
            raise ProviderNotConfigured("set GOOGLE_PLACES_API_KEY to use google_places discovery")
        key = self.settings.google_places_api_key.get_secret_value()  # type: ignore[union-attr]
        countries = query.countries or sorted(profile.primary_countries())[:1] or ["GR"]
        places = query.cities or [None]
        results: dict[str, CompanyIn] = {}
        requests = 0
        for country, place, (term, slug) in product(countries, places, self._terms(query, profile)):
            if len(results) >= query.limit:
                break
            where = (
                f"{place}, {COUNTRY_NAMES.get(country, country)}"
                if place
                else (COUNTRY_NAMES.get(country, country))
            )
            body = {
                "textQuery": f"{term} in {where}",
                "regionCode": country,
                "pageSize": min(20, query.limit),
            }
            response = self.http.post(
                self.settings.google_places_url,
                json=body,
                headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK},
            )
            requests += 1
            for item in response.json().get("places", []):
                company = self._to_company(item, country, slug or term, profile.name)
                if company is None or (query.require_website and not company.website):
                    continue
                results.setdefault(company.source_ref or company.name, company)
        companies = list(results.values())[: query.limit]
        cost = round(requests * self.settings.google_places_cost_per_request, 4)
        return DiscoveryOutcome(companies, requests_made=requests, cost_usd=cost)

    @staticmethod
    def _to_company(place: dict, country: str, industry: str, profile: str) -> CompanyIn | None:
        if place.get("businessStatus") not in (None, "OPERATIONAL"):
            return None
        name = (place.get("displayName") or {}).get("text")
        if not name:
            return None
        city = None
        country_code = country
        for comp in place.get("addressComponents", []):
            types = comp.get("types", [])
            if "locality" in types:
                city = comp.get("longText")
            if "country" in types and comp.get("shortText"):
                country_code = comp["shortText"]
        try:
            return CompanyIn(
                name=name,
                website=place.get("websiteUri"),
                country=country_code,
                city=city,
                industry=industry,
                keywords=[t for t in place.get("types", []) if t != "point_of_interest"],
                source="google_places",
                source_url=place.get("googleMapsUri"),
                source_ref=place.get("id"),
                profile=profile,
            )
        except ValueError:
            return None
