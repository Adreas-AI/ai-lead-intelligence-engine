"""Wikidata discovery provider.

Wikidata is a public, CC0-licensed knowledge base with an official SPARQL
endpoint intended for programmatic access, so results may be stored freely.
Coverage favours larger / notable companies; for local businesses use the
OpenStreetMap provider. Industry items (P452) come from the profile
(``industries.<slug>.wikidata_qids``).
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.http import HttpClient
from app.core.logging import get_logger
from app.domain.normalize import normalize_url
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

# ISO-3166 alpha-2 -> Wikidata country items. NL companies use Q55 (the country)
# while the ISO code lives on Q29999 (the Kingdom), so both are queried.
COUNTRY_QIDS: dict[str, tuple[str, ...]] = {
    "AT": ("Q40",), "BE": ("Q31",), "BG": ("Q219",), "HR": ("Q224",), "CY": ("Q229",),
    "CZ": ("Q213",), "DK": ("Q35",), "EE": ("Q191",), "FI": ("Q33",), "FR": ("Q142",),
    "DE": ("Q183",), "GR": ("Q41",), "HU": ("Q28",), "IE": ("Q27",), "IT": ("Q38",),
    "LV": ("Q211",), "LT": ("Q37",), "LU": ("Q32",), "MT": ("Q233",), "NL": ("Q55", "Q29999"),
    "PL": ("Q36",), "PT": ("Q45",), "RO": ("Q218",), "SK": ("Q214",), "SI": ("Q215",),
    "ES": ("Q29",), "SE": ("Q34",), "GB": ("Q145",), "CH": ("Q39",), "NO": ("Q20",),
    "RS": ("Q403",), "MK": ("Q221",), "AL": ("Q222",), "ME": ("Q236",), "TR": ("Q43",),
    "UA": ("Q212",), "EG": ("Q79",), "MA": ("Q1028",), "TN": ("Q948",), "IL": ("Q801",),
    "JO": ("Q810",), "LB": ("Q822",), "AE": ("Q878",), "SA": ("Q851",), "US": ("Q30",),
    "CA": ("Q16",),
}  # fmt: skip
_QID_TO_COUNTRY = {qid: code for code, qids in COUNTRY_QIDS.items() for qid in qids}


def build_query(industry_qids: list[str], country_qids: list[str], limit: int) -> str:
    industries = " ".join(f"wd:{q}" for q in industry_qids)
    countries = " ".join(f"wd:{q}" for q in country_qids)
    return f"""
SELECT ?company (SAMPLE(?industry) AS ?ind) (SAMPLE(?country) AS ?ctry)
       (SAMPLE(?website) AS ?site) (SAMPLE(?nameEn) AS ?name_en)
       (SAMPLE(?nameAny) AS ?name_any) (SAMPLE(?descEn) AS ?desc)
WHERE {{
  VALUES ?industry {{ {industries} }}
  VALUES ?country {{ {countries} }}
  ?company wdt:P452 ?industry ; wdt:P17 ?country .
  FILTER NOT EXISTS {{ ?company wdt:P576 ?dissolved . }}
  OPTIONAL {{ ?company wdt:P856 ?website . }}
  OPTIONAL {{ ?company rdfs:label ?nameEn . FILTER(LANG(?nameEn) = "en") }}
  OPTIONAL {{ ?company rdfs:label ?nameAny . }}
  OPTIONAL {{ ?company schema:description ?descEn . FILTER(LANG(?descEn) = "en") }}
}}
GROUP BY ?company
LIMIT {int(limit)}
""".strip()


class WikidataDiscoveryProvider(DiscoveryProvider):
    name = "wikidata"
    description = "Public CC0 knowledge base (SPARQL). No API key; best for notable companies."

    def __init__(self, settings: Settings, http: HttpClient | None = None):
        self.settings = settings
        # Wikidata asks for modest request rates from bots.
        self.http = http or HttpClient(settings, rate_per_second=0.5, timeout=60)

    def search(self, query: DiscoveryQuery, profile: Profile) -> DiscoveryOutcome:
        qid_to_slug: dict[str, str] = {}
        labels: dict[str, str] = {}
        for slug in selected_industries(query, profile):
            for qid, label in profile.industries[slug].wikidata_qids.items():
                qid_to_slug.setdefault(qid, slug)
                labels[qid] = label
        if not qid_to_slug:
            raise DiscoveryError(
                f"profile '{profile.name}' has no Wikidata industry mapping for these industries; "
                "try the openstreetmap or google_places provider"
            )
        countries = query.countries or sorted(profile.primary_countries())
        unsupported = [c for c in countries if c not in COUNTRY_QIDS]
        country_qids = [q for c in countries for q in COUNTRY_QIDS.get(c, ())]
        if not country_qids:
            raise DiscoveryError(f"no Wikidata country mapping for {countries}")

        # Over-fetch when keyword filtering is applied client-side.
        fetch_limit = min(query.limit * (4 if query.keywords else 1), 500)
        sparql = build_query(list(qid_to_slug), country_qids, fetch_limit)
        response = self.http.get(
            self.settings.wikidata_sparql_url,
            params={"query": sparql, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        try:
            bindings = response.json()["results"]["bindings"]
        except (ValueError, KeyError) as exc:
            raise DiscoveryError("unexpected Wikidata response format") from exc

        results: list[CompanyIn] = []
        for b in bindings:
            company = self._to_company(b, qid_to_slug, labels, profile.name)
            if company is None or not matches_keywords(company, query.keywords):
                continue
            results.append(company)
            if len(results) >= query.limit:
                break
        warnings = (
            [f"countries without Wikidata mapping skipped: {unsupported}"] if unsupported else []
        )
        log.info("wikidata_discovery", found=len(results), raw=len(bindings))
        return DiscoveryOutcome(results, requests_made=1, warnings=warnings)

    @staticmethod
    def _to_company(
        b: dict, qid_to_slug: dict[str, str], labels: dict[str, str], profile: str
    ) -> CompanyIn | None:
        def val(key: str) -> str | None:
            return b.get(key, {}).get("value")

        entity_url = val("company")
        name = val("name_en") or val("name_any")
        if not entity_url or not name:
            return None
        qid = entity_url.rsplit("/", 1)[-1]
        industry_qid = (val("ind") or "").rsplit("/", 1)[-1]
        country_qid = (val("ctry") or "").rsplit("/", 1)[-1]
        website = val("site")
        try:
            return CompanyIn(
                name=name,
                website=website if website and normalize_url(website) else None,
                country=_QID_TO_COUNTRY.get(country_qid),
                industry=qid_to_slug.get(industry_qid),
                description=val("desc"),
                keywords=[labels[industry_qid]] if industry_qid in labels else [],
                source="wikidata",
                source_url=entity_url,
                source_ref=qid,
                profile=profile,
            )
        except ValueError:
            return None
