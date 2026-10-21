"""Case-insensitive, word-bounded keyword matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from functools import lru_cache


def fold(text: str) -> str:
    """Lower-case and strip accents, so 'Κομμωτήριο' matches 'κομμωτηριο'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=4096)
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    # optional English plural: "gift voucher" also matches "gift vouchers"
    return re.compile(rf"(?<![\w-]){re.escape(fold(keyword))}(?:s|es)?(?![\w-])")


def find_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    folded = fold(text)
    return [kw for kw in keywords if keyword_pattern(kw).search(folded)]


def best_match(
    text: str | None, synonyms_by_slug: Mapping[str, Iterable[str]], default: str
) -> str:
    """Map free text to the slug whose longest synonym matches (exact slug wins).

    Returns "unknown" for empty text and ``default`` when nothing matches.
    """
    if not text or not text.strip():
        return "unknown"
    lowered = fold(text).replace("_", " ").strip()
    for slug in synonyms_by_slug:
        if lowered == slug.replace("_", " "):
            return slug
    best, best_len = default, 0
    for slug, synonyms in synonyms_by_slug.items():
        for syn in synonyms:
            if len(syn) > best_len and keyword_pattern(syn).search(lowered):
                best, best_len = slug, len(syn)
    return best
