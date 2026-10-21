"""Free-text search: "κρεοπωλεία στην Αθήνα", "dental clinics", "vegan restaurants"...

1. **Interpret** the query into a ``SearchPlan`` that is shown to the user before
   anything runs: what was understood, which OpenStreetMap tags / name keywords /
   search terms will be used, where, and which sources.
   * with an LLM (LLM_PROVIDER=openai): understands any wording in any language;
   * without: the built-in bilingual catalog (``business_catalog``), and if the
     query is not in it, a clearly-labelled search on business *names*.
2. **Build an ICP profile** from the plan and save it to ``profiles/`` so it can be
   inspected, edited and reused like any hand-written profile.
3. **Run**: discover (OpenStreetMap, plus Google Places when configured), store
   deduplicated leads, then enrich, score and draft outreach for the new ones.
"""

from __future__ import annotations

import json
import re

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Company, LeadStatus
from app.domain.business_catalog import BusinessCategory, match_categories
from app.domain.geo import COUNTRY_NAMES
from app.domain.keywords import fold
from app.domain.profiles import Profile, get_profile
from app.providers.discovery.base import DiscoveryError
from app.providers.discovery.registry import get_discovery_provider
from app.providers.llm import LLMError, LLMProvider
from app.schemas import (
    CompanyOut,
    DiscoveryRequest,
    DiscoveryResponse,
    PipelineResult,
    SearchPlan,
    SearchRequest,
    SearchRunRequest,
    SearchRunResult,
)
from app.services.enrichment import EnrichmentService
from app.services.leads import ingest
from app.services.outreach import OutreachService
from app.services.scoring import ScoringService
from app.services.usage import record_usage

log = get_logger(__name__)

OSM_KEYS = {"shop", "amenity", "office", "craft", "healthcare", "tourism", "leisure"}
LOCATION_SPLIT = re.compile(
    r"\s+(?:in|at|near|around|στην|στη|στο|στον|στα|στις|στους|σε|κοντά\s+σ\w*)\s+(.+)$", re.I
)
STOPWORDS = {
    "and", "the", "for", "with", "shops", "shop", "stores", "store", "companies", "company",
    "businesses", "business", "και", "για", "με", "της", "του", "των", "τα", "οι", "τις",
    "καταστηματα", "καταστημα", "μαγαζια", "μαγαζι", "εταιρειες", "εταιρεια", "επιχειρησεις",
}  # fmt: skip

GENERIC_BUSINESS_TYPES = {
    "independent": {
        "label": "Independent business",
        "fit": 1.0,
        "synonyms": ["family-run", "family business", "independent", "since", "our team",
                     "οικογενειακή επιχείρηση", "από το"],
    },
    "multi_location": {
        "label": "Several locations",
        "fit": 0.8,
        "synonyms": ["our stores", "our locations", "our branches", "branches",
                     "καταστήματά μας", "υποκαταστήματα"],
    },
    "franchise": {
        "label": "Franchise / chain",
        "fit": 0.5,
        "synonyms": ["franchise", "franchising", "chain", "store locator",
                     "δίκτυο καταστημάτων"],
    },
    "directory_or_vendor": {
        "label": "Directory / software vendor",
        "fit": 0.0,
        "target_model": False,
        "synonyms": ["directory", "listings", "software for", "saas", "οδηγός επιχειρήσεων"],
    },
}  # fmt: skip

GENERIC_SIGNALS = {
    "positive": {
        "contact_details": ["contact us", "contact", "επικοινωνία", "τηλέφωνο"],
        "opening_hours": ["opening hours", "open daily", "ωράριο", "ώρες λειτουργίας"],
        "online_booking": ["book online", "book now", "appointment", "booking", "reservations",
                           "ραντεβού", "κράτηση", "κρατήσεις"],
        "online_shop": ["online shop", "e-shop", "eshop", "shop online", "order online",
                        "ηλεκτρονικό κατάστημα", "παραγγελία"],
        "delivery": ["delivery", "home delivery", "διανομή", "κατ' οίκον", "αποστολή"],
        "products_or_services": ["our services", "services", "products", "price list",
                                 "υπηρεσίες", "προϊόντα", "τιμοκατάλογος"],
    },
    "negative": ["under construction", "coming soon", "temporarily closed"],
    "disqualify": ["permanently closed", "closed for good", "domain for sale",
                   "this domain is for sale", "κλειστό οριστικά"],
}  # fmt: skip

INTERPRET_SYSTEM_PROMPT = """You turn a salesperson's free-text description of the
businesses they want to find into a structured search plan for OpenStreetMap and a
maps search. The query may be in any language (often Greek).

Rules:
- categories: 1-4 entries. Each has an English plural "label", a "local_label" in the
  query's language, "osm_tags" (real, widely used OpenStreetMap tags written key=value,
  keys only from: shop, amenity, office, craft, healthcare, tourism, leisure) and
  "synonyms" (words for this business type in English and the query's language).
  If no OSM tag fits, return an empty osm_tags list.
- name_keywords: words that typically appear in such businesses' names, in the local
  language (used only when tags are missing). Max 5.
- search_terms: short maps-search queries in the local language. Max 4.
- website_keywords: words on a website that show the business offers this. Max 15.
- city: the place named in the query, or null. Never invent one.
Respond with JSON only:
{"label": str, "local_label": str, "categories": [{"label": str, "local_label": str,
 "osm_tags": [str], "synonyms": [str]}], "name_keywords": [str], "search_terms": [str],
 "website_keywords": [str], "city": str|null, "notes": str}"""

_GREEK_TO_LATIN = str.maketrans({
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i",
    "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s",
    "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
})  # fmt: skip


def slugify(text: str, max_len: int = 40) -> str:
    latin = fold(text).translate(_GREEK_TO_LATIN)
    slug = re.sub(r"[^a-z0-9]+", "_", latin).strip("_")
    return slug[:max_len].strip("_") or "search"


def split_location(query: str) -> tuple[str, str | None]:
    """'κρεοπωλεία στην Αθήνα' -> ('κρεοπωλεία', 'Αθήνα')."""
    m = LOCATION_SPLIT.search(query.strip())
    if not m:
        return query.strip(), None
    return query[: m.start()].strip(), m.group(1).strip(" .,")


def _plain_words(patterns: tuple[str, ...]) -> list[str]:
    return [p for p in patterns if "*" not in p]


def _name_stems(text: str) -> list[str]:
    stems = []
    for word in re.findall(r"\w+", fold(text)):
        if len(word) < 4 or word in STOPWORDS:
            continue
        stems.append(word[:-2] if len(word) >= 7 else word)  # tolerate inflection
    return list(dict.fromkeys(stems))[:4]


class SearchService:
    def __init__(self, db: Session, settings: Settings, llm: LLMProvider | None = None):
        self.db = db
        self.settings = settings
        self.llm = llm

    # ------------------------------------------------------------ interpretation
    def _wants_llm(self, req: SearchRequest) -> bool:
        if self.llm is None or self.llm.name == "mock":
            return False  # the mock LLM cannot understand free text
        return True if req.use_llm is None else req.use_llm

    def _sources(self, has_osm: bool) -> list[str]:
        sources = ["openstreetmap"] if has_osm else []
        if self.settings.google_places_api_key is not None:
            sources.append("google_places")
        return sources

    def _llm_plan(self, req: SearchRequest, what: str, city: str | None) -> dict | None:
        assert self.llm is not None
        payload = json.dumps(
            {"query": req.query, "country": COUNTRY_NAMES.get(req.country, req.country)},
            ensure_ascii=False,
        )
        try:
            result = self.llm.complete_json(
                INTERPRET_SYSTEM_PROMPT, payload, operation="search_interpretation"
            )
        except LLMError as exc:
            record_usage(
                self.db,
                provider=self.llm.name,
                operation="search_interpretation",
                model=self.llm.model,
                success=False,
            )
            log.warning("llm_interpretation_failed", error=str(exc))
            return None
        record_usage(
            self.db,
            provider=self.llm.name,
            operation="search_interpretation",
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=self.llm.cost_usd(result),
        )
        data = result.data
        cats = []
        for c in (data.get("categories") or [])[:4]:
            if not isinstance(c, dict) or not c.get("label"):
                continue
            tags = [
                t.strip()
                for t in c.get("osm_tags") or []
                if isinstance(t, str)
                and t.count("=") == 1
                and t.split("=")[0] in OSM_KEYS
                and t.split("=")[1]
            ]
            cats.append(
                {
                    "label": str(c["label"])[:80],
                    "local_label": str(c.get("local_label") or c["label"])[:80],
                    "osm_tags": tags[:4],
                    "synonyms": [str(s)[:60] for s in (c.get("synonyms") or [])][:12],
                }
            )
        if not cats:
            return None

        def strs(key: str, n: int) -> list[str]:
            return [str(x)[:60] for x in (data.get(key) or []) if isinstance(x, str) and x][:n]

        return {
            "label": str(data.get("label") or cats[0]["label"])[:80],
            "local_label": str(data.get("local_label") or "")[:80],
            "categories": cats,
            "name_keywords": strs("name_keywords", 5),
            "search_terms": strs("search_terms", 4),
            "website_keywords": strs("website_keywords", 15),
            "city": city or (data.get("city") if isinstance(data.get("city"), str) else None),
            "notes": str(data.get("notes") or "")[:300],
        }

    def interpret(self, req: SearchRequest, *, save: bool = False) -> tuple[SearchPlan, Profile]:
        """Build the plan + profile. ``save=True`` writes the profile to ``profiles/``."""
        what, city_in_query = split_location(req.query)
        city = req.city or city_in_query
        country_name = COUNTRY_NAMES.get(req.country, req.country)
        warnings: list[str] = []

        llm_plan = self._llm_plan(req, what, city) if self._wants_llm(req) else None
        if self._wants_llm(req) and llm_plan is None:
            warnings.append("LLM interpretation failed; used the built-in catalog instead.")

        if llm_plan:
            method = "llm"
            city = llm_plan["city"]
            categories = [c["label"] for c in llm_plan["categories"]]
            osm_tags = list(dict.fromkeys(t for c in llm_plan["categories"] for t in c["osm_tags"]))
            name_keywords = [] if osm_tags else (llm_plan["name_keywords"] or _name_stems(what))
            search_terms = llm_plan["search_terms"] or [what]
            website_keywords = llm_plan["website_keywords"]
            label = llm_plan["label"]
            local = f" ({llm_plan['local_label']})" if llm_plan["local_label"] else ""
            how = f"Interpreted by the LLM as: {label}{local}."
            if llm_plan["notes"]:
                how += f" Note: {llm_plan['notes']}"
            industries = {
                slugify(c["label"]): {
                    "label": c["label"],
                    "fit": 1.0,
                    "synonyms": list(dict.fromkeys([c["label"], c["local_label"], *c["synonyms"]])),
                    "osm_tags": c["osm_tags"],
                }
                for c in llm_plan["categories"]
            }
        else:
            matches: list[BusinessCategory] = match_categories(what)
            if matches:
                method = "catalog"
                categories = [f"{c.label_en} / {c.label_el}" for c in matches]
                osm_tags = list(dict.fromkeys(t for c in matches for t in c.osm_tags))
                name_keywords = []
                search_terms = list(dict.fromkeys([what, *(c.label_en for c in matches)]))
                website_keywords = [w for c in matches for w in _plain_words(c.patterns)]
                label = " + ".join(c.label_en for c in matches)
                how = (
                    "Matched the built-in business catalog: "
                    + "; ".join(f"{c.label_en} ({c.label_el})" for c in matches)
                    + "."
                )
                industries = {
                    c.key: {
                        "label": c.label_en,
                        "fit": 1.0,
                        "synonyms": list(
                            dict.fromkeys([c.label_en, c.label_el, *_plain_words(c.patterns)])
                        ),
                        "osm_tags": list(c.osm_tags),
                    }
                    for c in matches
                }
            else:
                method = "name_match"
                stems = _name_stems(what)
                if not stems:
                    raise DiscoveryError(f"could not understand {req.query!r}; try other words")
                categories = [what]
                osm_tags = []
                name_keywords = stems
                search_terms = [what]
                website_keywords = [w for w in re.findall(r"\w+", what) if len(w) > 3]
                label = what
                how = (
                    f"'{what}' is not in the built-in catalog, so the search looks for businesses "
                    f"whose NAME contains: {', '.join(stems)}. Results can be incomplete."
                )
                warnings.append(
                    "Name-based search only. Set OPENAI_API_KEY (LLM_PROVIDER=openai) for "
                    "smarter interpretation, or GOOGLE_PLACES_API_KEY for wider coverage."
                )
                industries = {
                    slugify(what): {"label": what, "fit": 1.0, "synonyms": [what], "osm_tags": []}
                }

        where = (
            f"{city} (within {req.radius_km:g} km), {country_name}"
            if city
            else (f"all of {country_name}")
        )
        sources = self._sources(bool(osm_tags or name_keywords))
        if not sources:
            raise DiscoveryError("no discovery source can handle this search")
        explanation = f"{how} Searching: {where}. Sources: {', '.join(sources)}."
        profile_name = "search_" + slugify(f"{label} {city or req.country}", 50)

        profile = self._build_profile(
            name=profile_name,
            query=req.query,
            label=label,
            method=method,
            industries=industries,
            website_keywords=website_keywords,
            name_keywords=name_keywords,
            search_terms=search_terms,
            country=req.country,
            city=city,
            offer=req.offer,
            save=save,
        )
        plan = SearchPlan(
            query=req.query,
            label=label,
            method=method,
            explanation=explanation,
            categories=categories,
            osm_tags=osm_tags,
            name_keywords=name_keywords,
            search_terms=search_terms,
            website_keywords=website_keywords[:15],
            country=req.country,
            city=city,
            radius_km=req.radius_km,
            profile_name=profile_name,
            sources=sources,
            warnings=warnings,
        )
        return plan, profile

    def _build_profile(
        self,
        *,
        name: str,
        query: str,
        label: str,
        method: str,
        industries: dict,
        website_keywords: list[str],
        name_keywords: list[str],
        search_terms: list[str],
        country: str,
        city: str | None,
        offer: str | None,
        save: bool,
    ) -> Profile:
        offering_keywords = list(
            dict.fromkeys(
                [*website_keywords, *(s for i in industries.values() for s in i["synonyms"])]
            )
        )
        data = {
            "name": name,
            "label": label[:80],
            "description": f"Generated from the search '{query}' (interpreted via {method}).",
            "weights": {
                "industry": 25,
                "business_type": 10,
                "geography": 20,
                "business_signals": 20,
                "offering": 15,
                "completeness": 10,
            },
            "thresholds": {"qualified": 60, "review": 40},
            "geography": {"primary_countries": [country], "cities": [city] if city else []},
            "industries": industries,
            "business_types": GENERIC_BUSINESS_TYPES,
            "signals": GENERIC_SIGNALS,
            "offering_categories": [
                {
                    "slug": "target_offering",
                    "label": label[:80],
                    "core": True,
                    "keywords": offering_keywords[:40] or [label],
                },
            ],
            "outreach": {
                "value_proposition": offer or self.settings.sender_value_proposition,
                "subject": "A quick idea for {name}",
                "relevance_default": "I thought it could be useful for {name}.",
            },
            "discovery": {"search_terms": search_terms, "osm_name_patterns": name_keywords},
        }
        profile = Profile.model_validate(data)
        if not save:
            return profile
        self.settings.profiles_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.profiles_dir / f"{name}.yaml"
        header = (
            f"# Generated by the free-text search: {query!r}\n"
            "# Edit freely (weights, keywords, pitch); re-running the same search overwrites it.\n"
        )
        path.write_text(
            header + yaml.safe_dump(profile.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return get_profile(name)

    # ----------------------------------------------------------------------- run
    def run(self, req: SearchRunRequest) -> SearchRunResult:
        plan, profile = self.interpret(req, save=True)
        responses: list[DiscoveryResponse] = []
        created_ids: list[int] = []
        errors: list[str] = []
        for source in plan.sources:
            query = DiscoveryRequest(
                provider=source,
                profile=profile.name,
                countries=[plan.country],
                cities=[plan.city] if plan.city else [],
                radius_km=plan.radius_km,
                limit=req.limit,
                require_website=req.require_website,
            )
            provider = get_discovery_provider(source, self.settings)
            try:
                outcome = provider.search(query, profile)
            except Exception as exc:  # one source failing must not hide the others
                log.warning("search_source_failed", source=source, error=str(exc))
                errors.append(f"{source}: {exc}")
                continue
            if outcome.requests_made:
                record_usage(
                    self.db,
                    provider=source,
                    operation="discovery",
                    requests=outcome.requests_made,
                    cost_usd=outcome.cost_usd,
                )
            report = None
            if outcome.companies:
                report = ingest(
                    self.db,
                    enumerate(outcome.companies, start=1),
                    source=source,
                    profile=profile.name,
                    label=f"search:{req.query}"[:255],
                )
                created_ids += report.created_ids
            else:
                self.db.commit()
            responses.append(
                DiscoveryResponse(
                    provider=source,
                    profile=profile.name,
                    found=len(outcome.companies),
                    results=outcome.companies,
                    import_report=report,
                )
            )
        if errors and not responses:
            raise DiscoveryError("; ".join(errors))
        plan.warnings += errors

        pipeline = None
        if req.enrich_and_score and created_ids:
            use_llm = self._wants_llm(req)
            n = len(created_ids)
            pipeline = PipelineResult(
                enrichment=EnrichmentService(self.db, self.settings, llm=self.llm).run_batch(
                    created_ids, limit=n, use_llm=use_llm
                ),
                scoring=ScoringService(self.db, self.settings, self.llm).run_batch(
                    created_ids, limit=n, use_llm=use_llm
                ),
                outreach=OutreachService(self.db, self.settings, self.llm).run_batch(
                    created_ids, limit=n, use_llm=use_llm
                ),
            )
        leads = self.db.scalars(
            select(Company)
            .where(Company.profile == profile.name, Company.status != LeadStatus.ARCHIVED)
            .order_by(Company.score.desc().nulls_last(), Company.id)
            .limit(500)
        )
        return SearchRunResult(
            plan=plan,
            discovery=responses,
            pipeline=pipeline,
            leads=[CompanyOut.model_validate(c) for c in leads],
        )
