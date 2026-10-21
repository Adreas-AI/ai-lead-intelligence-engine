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
from app.domain.taxonomy import COUNTRY_NAMES
from app.providers.discovery.base import (
    DiscoveryOutcome,
    DiscoveryProvider,
    DiscoveryQuery,
    ProviderNotConfigured,
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

DEFAULT_TERMS = ["agricultural products wholesaler", "fresh produce exporter", "food distributor"]


class GooglePlacesDiscoveryProvider(DiscoveryProvider):
    name = "google_places"
    description = "Google Places Text Search (paid API, requires GOOGLE_PLACES_API_KEY)."
    requires_credentials = True

    def __init__(self, settings: Settings, http: HttpClient | None = None):
        self.settings = settings
        self.http = http or HttpClient(settings, rate_per_second=5)

    def is_configured(self) -> bool:
        return self.settings.google_places_api_key is not None

    def _terms(self, query: DiscoveryQuery) -> list[str]:
        base = query.keywords or [i.replace("_", " ") for i in query.industries] or DEFAULT_TERMS
        if query.company_types:
            return [f"{b} {t.replace('_', ' ')}" for b, t in product(base, query.company_types)]
        return base

    def search(self, query: DiscoveryQuery) -> DiscoveryOutcome:
        if not self.is_configured():
            raise ProviderNotConfigured("set GOOGLE_PLACES_API_KEY to use google_places discovery")
        key = self.settings.google_places_api_key.get_secret_value()  # type: ignore[union-attr]
        countries = query.countries or ["GR"]
        results: dict[str, CompanyIn] = {}
        requests = 0
        for country, term in product(countries, self._terms(query)):
            if len(results) >= query.limit:
                break
            body = {
                "textQuery": f"{term} in {COUNTRY_NAMES.get(country, country)}",
                "regionCode": country,
                "pageSize": min(20, query.limit),
            }
            response = self.http.post(
                self.settings.google_places_url,
                json=body,
                headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK},
            )
            requests += 1
            for place in response.json().get("places", []):
                company = self._to_company(place, country, term)
                if company and company.source_ref not in results:
                    results[company.source_ref] = company  # type: ignore[index]
        companies = list(results.values())[: query.limit]
        cost = round(requests * self.settings.google_places_cost_per_request, 4)
        return DiscoveryOutcome(companies, requests_made=requests, cost_usd=cost)

    @staticmethod
    def _to_company(place: dict, country: str, term: str) -> CompanyIn | None:
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
                industry=term,
                keywords=[t for t in place.get("types", []) if t != "point_of_interest"],
                source="google_places",
                source_url=place.get("googleMapsUri"),
                source_ref=place.get("id"),
            )
        except ValueError:
            return None
