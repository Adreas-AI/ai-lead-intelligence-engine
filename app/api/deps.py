from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import session as db_session
from app.providers.llm import LLMConfigurationError, LLMProvider, get_llm_provider


def get_db() -> Iterator[Session]:
    yield from db_session.get_db()


def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """If API_KEY is configured, every request must send it as X-API-Key."""
    if settings.api_key is None:
        return
    expected = settings.api_key.get_secret_value()
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")


def get_llm(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    try:
        return get_llm_provider(settings)
    except LLMConfigurationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
