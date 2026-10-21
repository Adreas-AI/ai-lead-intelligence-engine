"""Name / URL normalisation used for duplicate detection."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit

# Legal-form suffixes stripped before comparing names (EU-focused, incl. Greek forms).
LEGAL_SUFFIXES = {
    "ltd",
    "limited",
    "llc",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "gmbh",
    "ag",
    "kg",
    "ug",
    "sa",
    "s a",
    "sas",
    "sarl",
    "srl",
    "s r l",
    "spa",
    "s p a",
    "sl",
    "s l",
    "bv",
    "b v",
    "nv",
    "n v",
    "oy",
    "ab",
    "as",
    "aps",
    "sp z o o",
    "zoo",
    "kft",
    "doo",
    "d o o",
    "ae",
    "a e",
    "ike",
    "i k e",
    "oe",
    "o e",
    "ee",
    "e e",
    "epe",
    "e p e",
    "lda",
    "group",
    "holding",
    "holdings",
    "and",
    "the",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_GENERIC_HOSTS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "google.com",
    "goo.gl",
    "wikipedia.org",
    "wikidata.org",
}


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    """'Olympia Fresh Produce S.A.' -> 'olympia fresh produce'."""
    text = strip_accents(name).lower().replace("&", " and ")
    text = _NON_ALNUM.sub(" ", text).strip()
    tokens = text.split()
    # Strip multi-token suffixes like "s a" / "i k e" from the end.
    changed = True
    while changed and tokens:
        changed = False
        for size in (4, 3, 2, 1):
            if len(tokens) > size and " ".join(tokens[-size:]) in LEGAL_SUFFIXES:
                tokens = tokens[:-size]
                changed = True
                break
    tokens = [t for t in tokens if t not in {"the", "and"}] or tokens
    return " ".join(tokens)


def normalize_url(url: str | None) -> str | None:
    if not url or not url.strip():
        return None
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    if not parts.hostname or "." not in parts.hostname:
        return None
    scheme = parts.scheme.lower() if parts.scheme in ("http", "https") else "https"
    path = parts.path.rstrip("/")
    return f"{scheme}://{parts.hostname.lower()}{path}"


def extract_domain(url: str | None) -> str | None:
    normalized = normalize_url(url)
    if not normalized:
        return None
    host = urlsplit(normalized).hostname or ""
    host = host.removeprefix("www.")
    if host in _GENERIC_HOSTS or any(host.endswith("." + g) for g in _GENERIC_HOSTS):
        return None  # a social profile is not an official company domain
    return host or None


def name_similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()
