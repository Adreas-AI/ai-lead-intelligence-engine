"""Ideal Customer Profiles (ICPs).

Everything that is specific to *who you are looking for* lives in a YAML file in
``profiles/``: industries, business types, website signals, offering categories,
target geography, scoring weights, qualification gates and the outreach pitch.
The engine itself is generic; switching vertical means writing a new profile.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import get_settings
from app.domain.geo import normalize_country
from app.domain.keywords import best_match, find_keywords


class ProfileError(ValueError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Industry(_Model):
    label: str
    fit: float = Field(ge=0, le=1, description="0..1 share of the industry weight")
    synonyms: list[str] = []
    osm_tags: list[str] = Field(
        default=[], description='OpenStreetMap tags, e.g. "shop=hairdresser"'
    )
    wikidata_qids: dict[str, str] = Field(
        default={}, description="Wikidata P452 items: QID -> label"
    )

    @field_validator("osm_tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        for tag in v:
            if tag.count("=") != 1 or not all(tag.split("=")):
                raise ValueError(f"OSM tag must look like key=value, got {tag!r}")
        return v


class BusinessType(_Model):
    label: str
    fit: float = Field(ge=0, le=1)
    target_model: bool = Field(
        default=True, description="Matches the business model you sell to (gets base points)"
    )
    synonyms: list[str] = []


class OfferingCategory(_Model):
    slug: str
    label: str
    core: bool = True
    keywords: list[str]


class Weights(_Model):
    industry: int = 25
    business_type: int = 20
    geography: int = 15
    business_signals: int = 15
    offering: int = 15
    completeness: int = 10

    @model_validator(mode="after")
    def _sum_to_100(self) -> Self:
        total = sum(self.model_dump().values())
        if total != 100:
            raise ValueError(f"weights must sum to 100, got {total}")
        return self


class Thresholds(_Model):
    qualified: int = Field(default=70, ge=1, le=100)
    review: int = Field(default=50, ge=0, le=100)


class Gates(_Model):
    require_verified_website: bool = True
    require_target_market: bool = True


class Geography(_Model):
    primary_countries: list[str] = []
    secondary_countries: list[str] = []
    cities: list[str] = Field(default=[], description="Optional focus cities (full points)")

    @field_validator("primary_countries", "secondary_countries", mode="before")
    @classmethod
    def _iso(cls, v: list) -> list[str]:
        codes = []
        for c in v or []:
            if c is False:  # YAML 1.1 reads an unquoted NO (Norway) as false
                c = "NO"
            code = normalize_country(c)
            if code is None:
                raise ValueError(f"unknown country {c!r}")
            codes.append(code)
        return codes


class Signals(_Model):
    positive: dict[str, list[str]] = {}
    negative: list[str] = []
    disqualify: list[str] = Field(
        default=[], description="Phrases that rule a lead out (e.g. 'permanently closed')"
    )


class Outreach(_Model):
    value_proposition: str | None = None
    subject: str = "A quick idea for {name}"
    relevance_default: str = "There may be a fit for {name}."
    relevance_by_type: dict[str, str] = {}
    signal_lines: dict[str, str] = {}


class Discovery(_Model):
    search_terms: list[str] = Field(default=[], description="Google Places text queries")
    osm_name_patterns: list[str] = Field(
        default=[], description="Words matched against OpenStreetMap business names"
    )


class Profile(_Model):
    name: str
    label: str
    description: str
    weights: Weights = Weights()
    thresholds: Thresholds = Thresholds()
    gates: Gates = Gates()
    geography: Geography = Geography()
    industries: dict[str, Industry]
    business_types: dict[str, BusinessType]
    signals: Signals = Signals()
    offering_categories: list[OfferingCategory] = []
    outreach: Outreach = Outreach()
    discovery: Discovery = Discovery()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.thresholds.review > self.thresholds.qualified:
            raise ValueError("thresholds.review must be <= thresholds.qualified")
        reserved = {"unknown", "other"}
        if reserved & (set(self.industries) | set(self.business_types)):
            raise ValueError("'unknown' and 'other' are reserved slugs")
        for t in self.outreach.relevance_by_type:
            if t not in self.business_types:
                raise ValueError(f"outreach.relevance_by_type refers to unknown type {t!r}")
        return self

    # ---------------------------------------------------------------- helpers
    def classify_industry(self, text: str | None) -> str:
        return best_match(text, {k: v.synonyms for k, v in self.industries.items()}, "other")

    def classify_business_type(self, text: str | None) -> str:
        return best_match(text, {k: v.synonyms for k, v in self.business_types.items()}, "unknown")

    def industry_fit(self, slug: str) -> float:
        item = self.industries.get(slug)
        return item.fit if item else 0.0

    def type_info(self, slug: str) -> BusinessType | None:
        return self.business_types.get(slug)

    def category(self, label: str) -> OfferingCategory | None:
        return next((c for c in self.offering_categories if c.label == label), None)

    def infer_business_type(self, text: str) -> str | None:
        """Best-fitting *target* business type whose synonyms appear in ``text``."""
        best, best_fit = None, -1.0
        for slug, t in self.business_types.items():
            if (
                t.target_model
                and t.synonyms
                and find_keywords(text, t.synonyms)
                and t.fit > best_fit
            ):
                best, best_fit = slug, t.fit
        return best

    def primary_countries(self) -> frozenset[str]:
        return frozenset(self.geography.primary_countries) or get_settings().primary_countries

    def secondary_countries(self) -> frozenset[str]:
        if self.geography.primary_countries:
            return frozenset(self.geography.secondary_countries)
        return get_settings().secondary_countries

    def value_proposition(self) -> str:
        return self.outreach.value_proposition or get_settings().sender_value_proposition

    def summary(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "industries": {k: v.label for k, v in self.industries.items()},
            "business_types": {k: v.label for k, v in self.business_types.items()},
            "countries": sorted(self.primary_countries()),
            "cities": self.geography.cities,
        }


def _profiles_dir() -> Path:
    return get_settings().profiles_dir


@lru_cache(maxsize=64)
def _load(path: Path, mtime: float) -> Profile:  # mtime busts the cache on edits
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path.name}: invalid YAML: {exc}") from exc
    data.setdefault("name", path.stem)
    if data["name"] != path.stem:
        raise ProfileError(f"{path.name}: 'name' must match the file name ({path.stem})")
    try:
        return Profile.model_validate(data)
    except ValueError as exc:
        raise ProfileError(f"{path.name}: {exc}") from exc


def list_profile_names() -> list[str]:
    return sorted(p.stem for p in _profiles_dir().glob("*.yaml") if not p.stem.startswith("_"))


def get_profile(name: str | None = None) -> Profile:
    name = name or get_settings().default_profile
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ProfileError(f"invalid profile name {name!r}")
    path = _profiles_dir() / f"{name}.yaml"
    if not path.exists():
        raise ProfileError(f"unknown profile {name!r}; available: {list_profile_names()}")
    return _load(path, path.stat().st_mtime)
