"""Application settings, loaded from environment variables / `.env`.

Secrets are typed as ``SecretStr`` so they never appear in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EU27 = "AT,BE,BG,HR,CY,CZ,DK,EE,FI,FR,DE,GR,HU,IE,IT,LV,LT,LU,MT,NL,PL,PT,RO,SK,SI,ES,SE"


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(v.strip().upper() for v in value.split(",") if v.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Application -------------------------------------------------------
    app_name: str = "AI Lead Intelligence Engine"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = False
    database_url: str = "postgresql+psycopg://leads:leads@localhost:5432/leads"
    # Optional shared secret for the REST API (sent as the X-API-Key header).
    api_key: SecretStr | None = None
    api_rate_limit_per_minute: int = 120

    # --- LLM ----------------------------------------------------------------
    # "none" disables the LLM layer, "mock" is deterministic and free, "openai" is real.
    llm_provider: Literal["none", "mock", "openai"] = "mock"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 30.0
    # USD per 1M tokens. Check your provider's current price list and override.
    openai_input_cost_per_1m: float = 0.15
    openai_output_cost_per_1m: float = 0.60

    # --- Ideal Customer Profiles ------------------------------------------------
    # YAML files describing who you are looking for (see profiles/README.md).
    profiles_dir: Path = PROJECT_ROOT / "profiles"
    default_profile: str = "restaurants"

    # --- Discovery ------------------------------------------------------------
    wikidata_sparql_url: str = "https://query.wikidata.org/sparql"
    # OpenStreetMap (ODbL): Overpass for POI search, Nominatim for geocoding cities.
    # Comma-separated public Overpass instances, tried in order when one is busy/down.
    overpass_urls: str = (
        "https://overpass-api.de/api/interpreter,"
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter"
    )
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    osm_timeout_seconds: float = 120.0
    google_places_api_key: SecretStr | None = None
    google_places_url: str = "https://places.googleapis.com/v1/places:searchText"
    # USD per request: requesting websiteUri bills the Text Search *Enterprise* SKU
    # ($35 per 1,000 after 1,000 free calls/month, Oct 2026). Check the current price list.
    google_places_cost_per_request: float = 0.035

    # --- Outbound HTTP / enrichment -------------------------------------------
    # "auto": fetch real websites; synthetic *.example domains use local fixtures.
    # "offline": never touch the network (fixtures only).
    enrichment_mode: Literal["auto", "offline"] = "auto"
    http_user_agent: str = (
        "AILeadIntelligenceEngine/0.1 (+https://github.com/Adreas-AI/ai-lead-intelligence-engine)"
    )
    http_timeout_seconds: float = 10.0
    http_max_retries: int = 3
    outbound_requests_per_second: float = 1.0
    max_page_bytes: int = 2_000_000
    demo_data_dir: Path = PROJECT_ROOT / "data" / "demo"

    # --- Scoring defaults (a profile's own geography takes precedence) ---------
    target_countries_primary: str = EU27
    target_countries_secondary: str = "GB,CH,NO,RS,MK,AL,ME,TR,UA,EG,MA,TN,IL,JO,LB,AE,SA"

    # --- Outreach ------------------------------------------------------------
    sender_name: str = "Alex Example"
    sender_company: str = "Your Company"
    # Fallback pitch; each profile can define its own outreach.value_proposition.
    sender_value_proposition: str = "a service that helps businesses like yours win more customers"

    # --- CRM -----------------------------------------------------------------
    hubspot_access_token: SecretStr | None = None
    hubspot_base_url: str = "https://api.hubapi.com"
    export_dir: Path = PROJECT_ROOT / "exports"

    @field_validator(
        "api_key", "openai_api_key", "google_places_api_key", "hubspot_access_token", mode="before"
    )
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        # `OPENAI_API_KEY=` in .env means "not configured", not "an empty key".
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @property
    def overpass_endpoints(self) -> list[str]:
        return [u.strip() for u in self.overpass_urls.split(",") if u.strip()]

    @property
    def primary_countries(self) -> frozenset[str]:
        return _csv_set(self.target_countries_primary)

    @property
    def secondary_countries(self) -> frozenset[str]:
        return _csv_set(self.target_countries_secondary)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
