"""CRM providers. HubSpot is optional and only used when a token is configured."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.core.config import Settings
from app.core.http import HttpClient


class CrmError(Exception):
    pass


class CrmProvider(ABC):
    name: str

    @abstractmethod
    def find_by_domain(self, domain: str) -> str | None: ...

    @abstractmethod
    def create_company(self, properties: dict[str, Any]) -> str: ...

    @abstractmethod
    def update_company(self, external_id: str, properties: dict[str, Any]) -> None: ...


class HubSpotProvider(CrmProvider):
    """HubSpot CRM v3 companies API, authenticated with a private-app access token."""

    name = "hubspot"

    def __init__(self, settings: Settings, http: HttpClient | None = None):
        if settings.hubspot_access_token is None:
            raise CrmError("set HUBSPOT_ACCESS_TOKEN to enable HubSpot sync")
        self.base = settings.hubspot_base_url.rstrip("/")
        self.http = http or HttpClient(settings, rate_per_second=8)  # HubSpot: ~10 req/s burst
        self._headers = {
            "Authorization": f"Bearer {settings.hubspot_access_token.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.http.request(
                method, f"{self.base}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise CrmError(
                f"HubSpot {method} {path} -> {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise CrmError(f"HubSpot {method} {path} failed: {exc}") from exc
        return response.json() if response.content else {}

    def find_by_domain(self, domain: str) -> str | None:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}
            ],
            "properties": ["domain", "name"],
            "limit": 1,
        }
        data = self._call("POST", "/crm/v3/objects/companies/search", json=body)
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    def create_company(self, properties: dict[str, Any]) -> str:
        data = self._call("POST", "/crm/v3/objects/companies", json={"properties": properties})
        return str(data["id"])

    def update_company(self, external_id: str, properties: dict[str, Any]) -> None:
        self._call(
            "PATCH", f"/crm/v3/objects/companies/{external_id}", json={"properties": properties}
        )
