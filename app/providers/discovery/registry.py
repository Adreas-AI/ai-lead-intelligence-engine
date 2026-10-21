from __future__ import annotations

from app.core.config import Settings
from app.providers.discovery.base import DiscoveryError, DiscoveryProvider
from app.providers.discovery.google_places import GooglePlacesDiscoveryProvider
from app.providers.discovery.mock import MockDiscoveryProvider
from app.providers.discovery.wikidata import WikidataDiscoveryProvider

PROVIDERS: dict[str, type[DiscoveryProvider]] = {
    "mock": MockDiscoveryProvider,
    "wikidata": WikidataDiscoveryProvider,
    "google_places": GooglePlacesDiscoveryProvider,
}


def get_discovery_provider(name: str, settings: Settings) -> DiscoveryProvider:
    try:
        cls = PROVIDERS[name]
    except KeyError as exc:
        raise DiscoveryError(f"unknown provider {name!r}; available: {sorted(PROVIDERS)}") from exc
    return cls(settings)  # type: ignore[call-arg]


def list_discovery_providers(settings: Settings) -> list[DiscoveryProvider]:
    return [cls(settings) for cls in PROVIDERS.values()]  # type: ignore[call-arg]
