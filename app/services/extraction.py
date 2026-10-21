"""HTML -> structured, evidence-backed company facts.

Pure functions, no I/O. Everything returned is traceable to the page content;
nothing is guessed. Fields that cannot be found stay ``None`` / empty.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.domain.geo import COUNTRY_NAMES, REGION_NAMES, normalize_country
from app.domain.keywords import find_keywords
from app.domain.normalize import normalize_name, strip_accents
from app.domain.profiles import Profile

ABOUT_HINTS = ("about", "company", "who-we-are", "who_we_are", "our-story", "profile", "products")
_WS = re.compile(r"\s+")
# ccTLD -> ISO code where they differ or are common in the target markets.
CCTLD_OVERRIDES = {"uk": "GB", "gr": "GR", "el": "GR"}
GENERIC_TLDS = {"com", "net", "org", "eu", "info", "biz", "co", "io", "example", "shop", "store"}


@dataclass
class PageFacts:
    title: str | None = None
    meta_description: str | None = None
    site_name: str | None = None
    lang: str | None = None
    jsonld_name: str | None = None
    jsonld_description: str | None = None
    address_country: str | None = None
    address_locality: str | None = None
    text: str = ""
    about_links: list[str] = field(default_factory=list)
    english_url: str | None = None  # <link rel="alternate" hreflang="en">


@dataclass
class ExtractedProfile:
    products: list[str]
    categories: list[str]
    business_signals: list[str]
    negative_signals: list[str]
    inferred_company_type: str | None
    markets_mentioned: list[str]


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _WS.sub(" ", text).strip()
    return cleaned or None


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                yield item


_BUSINESS_TYPES = {
    "Organization", "Corporation", "LocalBusiness", "Store", "Restaurant", "CafeOrCoffeeShop",
    "BarOrPub", "Bakery", "HairSalon", "BeautySalon", "NailSalon", "DaySpa", "Dentist",
    "MedicalClinic", "Physician", "Pharmacy", "Hotel", "LodgingBusiness", "AutoRepair",
    "Attorney", "LegalService", "AccountingService", "RealEstateAgent", "TravelAgency",
}  # fmt: skip


def _is_business_type(t: str) -> bool:
    return t in _BUSINESS_TYPES or t.endswith(("Business", "Store", "Establishment", "Service"))


def parse_page(html: str, base_url: str) -> PageFacts:
    soup = BeautifulSoup(html, "html.parser")
    facts = PageFacts()
    if soup.title and soup.title.string:
        facts.title = _clean(soup.title.string)
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content") and not facts.meta_description:
            facts.meta_description = _clean(tag["content"])
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    if og_site and og_site.get("content"):
        facts.site_name = _clean(og_site["content"])
    if soup.html and soup.html.get("lang"):
        facts.lang = soup.html["lang"].split("-")[0].lower()

    for item in _iter_jsonld(soup):
        types = item.get("@type", [])
        types = types if isinstance(types, list) else [types]
        if not any(_is_business_type(str(t)) for t in types):
            continue
        facts.jsonld_name = facts.jsonld_name or _clean(item.get("name"))
        facts.jsonld_description = facts.jsonld_description or _clean(item.get("description"))
        address = item.get("address")
        if isinstance(address, list):
            address = address[0] if address else None
        if isinstance(address, dict):
            country = address.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            facts.address_country = facts.address_country or _clean(country)
            facts.address_locality = facts.address_locality or _clean(
                address.get("addressLocality")
            )

    base_host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    for link in soup.find_all("link", rel="alternate", hreflang=True):
        if link["hreflang"].lower().startswith("en") and link.get("href"):
            href = urljoin(base_url, link["href"])
            if (urlsplit(href).hostname or "").removeprefix("www.") == base_host:
                facts.english_url = href
                break
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        host = (urlsplit(href).hostname or "").removeprefix("www.")
        path = urlsplit(href).path.lower()
        if (
            host == base_host
            and any(h in path for h in ABOUT_HINTS)
            and href not in facts.about_links
        ):
            facts.about_links.append(href.split("#")[0])

    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        tag.decompose()
    facts.text = _WS.sub(" ", soup.get_text(" ")).strip()
    return facts


def extract_profile(text: str, profile: Profile) -> ExtractedProfile:
    """Evidence found in ``text``, interpreted through the ICP ``profile``."""
    products: list[str] = []
    categories: list[str] = []
    for cat in profile.offering_categories:
        hits = find_keywords(text, cat.keywords)
        if hits:
            categories.append(cat.label)
            products.extend(h for h in hits if h not in products)
    signals = [name for name, kws in profile.signals.positive.items() if find_keywords(text, kws)]
    negative = find_keywords(text, [*profile.signals.negative, *profile.signals.disqualify])
    inferred_type = profile.infer_business_type(text)

    lowered = strip_accents(text).lower()
    markets = [
        name
        for name in [*COUNTRY_NAMES.values(), *REGION_NAMES]
        if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", lowered)
    ]
    return ExtractedProfile(
        products[:25], categories, signals, negative, inferred_type, markets[:20]
    )


def detect_country(facts: PageFacts, url: str) -> tuple[str | None, str | None]:
    """Return (ISO code, evidence). Structured data beats ccTLD; never guesses beyond that."""
    if facts.address_country:
        code = normalize_country(facts.address_country)
        if code:
            return code, f"schema.org address: {facts.address_country}"
    host = urlsplit(url).hostname or ""
    tld = host.rsplit(".", 1)[-1].lower()
    if tld not in GENERIC_TLDS:
        code = CCTLD_OVERRIDES.get(tld) or normalize_country(tld)
        if code:
            return code, f"country-code domain .{tld} (inferred)"
    return None, None


def name_matches_site(company_name: str, facts: PageFacts) -> bool:
    """Is the page plausibly the official site of this company?"""
    target = normalize_name(company_name)
    if not target:
        return False
    candidates = " ".join(
        filter(None, [facts.title, facts.site_name, facts.jsonld_name, facts.text[:3000]])
    )
    haystack = normalize_name(candidates)
    if target in haystack:
        return True
    tokens = [t for t in target.split() if len(t) > 3]
    return bool(tokens) and sum(t in haystack for t in tokens) / len(tokens) >= 0.6


def extractive_summary(facts: PageFacts, max_chars: int = 320) -> str | None:
    """Summary built only from the company's own words (no generation)."""
    candidate = facts.jsonld_description or facts.meta_description
    if not candidate:
        sentences = re.split(r"(?<=[.!?])\s+", facts.text)
        candidate = next(
            (s for s in sentences if 60 <= len(s) <= 400 and not s.lower().startswith("cookie")),
            None,
        )
    if not candidate:
        return None
    if len(candidate) > max_chars:
        candidate = candidate[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return candidate
