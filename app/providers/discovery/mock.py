"""Mock discovery provider backed by a synthetic JSON fixture (no network, no keys)."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.domain.taxonomy import classify_company_type, classify_industry
from app.providers.discovery.base import (
    DiscoveryOutcome,
    DiscoveryProvider,
    DiscoveryQuery,
    matches_keywords,
)
from app.schemas import CompanyIn


class MockDiscoveryProvider(DiscoveryProvider):
    name = "mock"
    description = "Synthetic agri-food companies for demos and tests (clearly labelled)."

    def __init__(self, settings: Settings, fixture: Path | None = None):
        self.fixture = fixture or settings.demo_data_dir / "mock_discovery.json"

    def search(self, query: DiscoveryQuery) -> DiscoveryOutcome:
        raw = json.loads(self.fixture.read_text(encoding="utf-8"))
        results: list[CompanyIn] = []
        for item in raw["companies"]:
            company = CompanyIn(**item, source="mock", is_synthetic=True)
            if query.countries and company.country not in query.countries:
                continue
            if query.industries and classify_industry(company.industry) not in {
                classify_industry(i) for i in query.industries
            }:
                continue
            if query.company_types and classify_company_type(company.company_type) not in {
                classify_company_type(t) for t in query.company_types
            }:
                continue
            if not matches_keywords(company, query.keywords):
                continue
            results.append(company)
            if len(results) >= query.limit:
                break
        return DiscoveryOutcome(results)
