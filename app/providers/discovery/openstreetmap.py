"""OpenStreetMap discovery provider (free, no API key).

Finds local businesses (shops, restaurants, clinics, offices, trades...) through
the Overpass API, using the OSM tags defined for each industry in the profile
(e.g. ``shop=butcher``) and/or business-name patterns. Cities are geocoded with
Nominatim and searched within ``radius_km`` of their centre.

Data © OpenStreetMap contributors, available under the ODbL. Keep attribution
when you display or share results, and respect the public servers' usage
policies (this client identifies itself and is rate-limited).
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.http import HttpClient
from app.core.logging import get_logger
from app.core.retry import RetryPolicy
from app.domain.profiles import Profile
from app.providers.discovery.base import (
    DiscoveryError,
    DiscoveryOutcome,
    DiscoveryProvider,
    DiscoveryQuery,
    matches_keywords,
    selected_industries,
)
from app.schemas import CompanyIn

log = get_logger(__name__)

POI_KEYS = "shop|amenity|office|craft|healthcare|tourism|leisure"
WEBSITE_FILTER = '[~"^(website|contact:website|url)$"~"."]'
GREEK_VARIANTS = {
    "α": "αάΑΆ", "ε": "εέΕΈ", "η": "ηήΗΉ", "ι": "ιίϊΐΙΊΪ", "ο": "οόΟΌ", "υ": "υύϋΰΥΎΫ",
    "ω": "ωώΩΏ", "σ": "σςΣ",
}  # fmt: skip


def name_regex(word: str) -> str:
    """Case- and accent-insensitive regex for an Overpass name filter."""
    from app.domain.keywords import fold

    out = []
    for ch in fold(word):
        if ch in GREEK_VARIANTS:
            out.append(f"[{GREEK_VARIANTS[ch]}]")
        elif ch.isalpha():
            out.append(f"[{ch}{ch.upper()}]")
        elif ch.isspace():
            out.append(r"\\s+")
        else:
            out.append("\\\\" + ch if not ch.isalnum() else ch)
    return "".join(out)


class OpenStreetMapDiscoveryProvider(DiscoveryProvider):
    name = "openstreetmap"
    description = (
        "OpenStreetMap via Overpass (free, no key, ODbL). Best for local businesses: "
        "shops, restaurants, clinics, offices, trades."
    )

    def __init__(self, settings: Settings, http: HttpClient | None = None):
        self.settings = settings
        # Public Overpass servers are often busy: back off patiently, then try the next one.
        self.http = http or HttpClient(
            settings,
            rate_per_second=1.0,
            timeout=settings.osm_timeout_seconds,
            retry_policy=RetryPolicy(max_attempts=2, base_delay=5.0, max_delay=30.0),
        )
        self._geocache: dict[tuple[str, str], tuple[float, float]] = {}

    def geocode(self, city: str, country: str) -> tuple[float, float]:
        key = (city.lower(), country)
        if key not in self._geocache:
            response = self.http.get(
                self.settings.nominatim_url,
                params={"q": city, "countrycodes": country.lower(), "format": "jsonv2", "limit": 1},
            )
            hits = response.json()
            if not hits:
                raise DiscoveryError(f"could not find the city {city!r} in {country}")
            self._geocache[key] = (float(hits[0]["lat"]), float(hits[0]["lon"]))
        return self._geocache[key]

    def build_query(self, query: DiscoveryQuery, profile: Profile) -> tuple[str, dict[str, str]]:
        """Overpass QL plus a map 'key=value' -> industry slug used to label results."""
        tag_to_slug: dict[str, str] = {}
        for slug in selected_industries(query, profile):
            for tag in profile.industries[slug].osm_tags:
                tag_to_slug.setdefault(tag, slug)
        name_patterns = profile.discovery.osm_name_patterns
        if not tag_to_slug and not name_patterns:
            raise DiscoveryError(
                f"profile '{profile.name}' defines no OpenStreetMap tags or name patterns"
            )
        countries = query.countries or sorted(profile.primary_countries())[:1]
        if not countries:
            raise DiscoveryError("choose a country for OpenStreetMap discovery")

        web = WEBSITE_FILTER if query.require_website else ""
        selectors = [f'["{k}"="{v}"]' for k, v in (t.split("=", 1) for t in tag_to_slug)]
        selectors += [f'["name"~"{name_regex(p)}"][~"^({POI_KEYS})$"~"."]' for p in name_patterns]

        areas: list[str] = []
        header = ""
        if query.cities:
            radius = int(query.radius_km * 1000)
            for city in query.cities:
                lat, lon = self.geocode(city, countries[0])
                areas.append(f"(around:{radius},{lat:.6f},{lon:.6f})")
        else:
            names = []
            for i, country in enumerate(countries):
                header += f'area["ISO3166-1"="{country}"][admin_level=2]->.c{i};'
                names.append(f"(area.c{i})")
            areas = names
        body = "".join(f"nwr{sel}{web}{area};" for sel in selectors for area in areas)
        timeout = int(self.settings.osm_timeout_seconds)
        ql = f"[out:json][timeout:{timeout}];{header}({body});out tags center {query.limit * 3};"
        return ql, tag_to_slug

    def search(self, query: DiscoveryQuery, profile: Profile) -> DiscoveryOutcome:
        ql, tag_to_slug = self.build_query(query, profile)
        requests = len(query.cities)  # geocoding calls (cached per city)
        elements = None
        failures: list[str] = []
        for endpoint in self.settings.overpass_endpoints:
            requests += 1
            try:
                elements = self.http.post(endpoint, data={"data": ql}).json()["elements"]
                break
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                host = endpoint.split("/")[2]
                log.warning("overpass_endpoint_failed", endpoint=host, error=str(exc)[:200])
                failures.append(f"{host}: {type(exc).__name__}")
        if elements is None:
            raise DiscoveryError(
                "OpenStreetMap servers are busy or unreachable right now "
                f"({'; '.join(failures)}). Please try again in a minute."
            )

        country = (query.countries or sorted(profile.primary_countries())[:1])[0]
        # Results found by name pattern (no tag) belong to the searched industry.
        fallback = (
            selected_industries(query, profile)[0] if profile.discovery.osm_name_patterns else None
        )
        results: list[CompanyIn] = []
        seen: set[str] = set()
        for el in elements:
            company = self._to_company(el, tag_to_slug, country, profile.name, fallback)
            if company is None or not matches_keywords(company, query.keywords):
                continue
            dedup_key = company.website or company.name.lower()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            results.append(company)
            if len(results) >= query.limit:
                break
        log.info("osm_discovery", found=len(results), raw=len(elements))
        return DiscoveryOutcome(results, requests_made=requests)

    @staticmethod
    def _to_company(
        el: dict,
        tag_to_slug: dict[str, str],
        country: str,
        profile: str,
        fallback_industry: str | None = None,
    ) -> CompanyIn | None:
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("name:en") or tags.get("brand")
        if not name:
            return None
        industry = next(
            (
                slug
                for tag, slug in tag_to_slug.items()
                if tags.get(tag.split("=", 1)[0]) == tag.split("=", 1)[1]
            ),
            fallback_industry,
        )
        kind = next((f"{k}={tags[k]}" for k in POI_KEYS.split("|") if k in tags), None)
        keywords = [kind] if kind else []
        for extra in ("cuisine", "healthcare:speciality", "craft", "shop"):
            if tags.get(extra) and extra != kind:
                keywords += [v.strip() for v in tags[extra].split(";") if v.strip()]
        osm_type, osm_id = el.get("type"), el.get("id")
        try:
            return CompanyIn(
                name=name,
                website=tags.get("website") or tags.get("contact:website") or tags.get("url"),
                country=tags.get("addr:country")
                if len(tags.get("addr:country", "")) == 2
                else country,
                city=tags.get("addr:city"),
                industry=industry,
                keywords=list(dict.fromkeys(keywords))[:10],
                source="openstreetmap",
                source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                source_ref=f"osm:{osm_type}/{osm_id}",
                profile=profile,
            )
        except ValueError:
            return None
