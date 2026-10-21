from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.schemas import CompanyIn, DiscoveryRequest

DiscoveryQuery = DiscoveryRequest


class DiscoveryError(Exception):
    pass


class ProviderNotConfigured(DiscoveryError):
    pass


@dataclass
class DiscoveryOutcome:
    companies: list[CompanyIn]
    requests_made: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)


class DiscoveryProvider(ABC):
    name: str
    description: str
    requires_credentials: bool = False

    def is_configured(self) -> bool:
        return True

    @abstractmethod
    def search(self, query: DiscoveryQuery) -> DiscoveryOutcome: ...


def matches_keywords(company: CompanyIn, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = " ".join(
        filter(None, [company.name, company.description, company.industry, *company.keywords])
    ).lower()
    return any(k.lower() in haystack for k in keywords)
