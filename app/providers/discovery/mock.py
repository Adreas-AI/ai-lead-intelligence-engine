"""Mock discovery provider backed by a synthetic JSON fixture (no network, no keys)."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.domain.profiles import Profile
from app.providers.discovery.base import (
    DiscoveryOutcome,
    DiscoveryProvider,
    DiscoveryQuery,
    matches_keywords,
    selected_industries,
)
from app.schemas import CompanyIn


class MockDiscoveryProvider(DiscoveryProvider):
    name = "mock"
    description = "Synthetic companies for demos and tests (clearly labelled, no network)."

    def __init__(self, settings: Settings, fixture: Path | None = None):
        self.fixture = fixture or settings.demo_data_dir / "mock_discovery.json"

    def search(self, query: DiscoveryQuery, profile: Profile) -> DiscoveryOutcome:
        raw = json.loads(self.fixture.read_text(encoding="utf-8"))
        industries = set(selected_industries(query, profile)) if query.industries else None
        types = {profile.classify_business_type(t) for t in query.company_types}
        results: list[CompanyIn] = []
        for item in raw["companies"]:
            if item.get("profile", profile.name) != profile.name:
                continue
            company = CompanyIn(**item, source="mock", is_synthetic=True)
            if query.countries and company.country not in query.countries:
                continue
            if industries and profile.classify_industry(company.industry) not in industries:
                continue
            if types and profile.classify_business_type(company.company_type) not in types:
                continue
            if not matches_keywords(company, query.keywords):
                continue
            results.append(company)
            if len(results) >= query.limit:
                break
        return DiscoveryOutcome(results)
