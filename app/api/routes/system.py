from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import get_db
from app.core.config import Settings, get_settings
from app.schemas import AnalyticsSummary
from app.services import analytics

router = APIRouter(tags=["system"])
public_router = APIRouter(tags=["system"])


@public_router.get("/health")
def health(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "version": __version__,
        "llm_provider": settings.llm_provider,
        "integrations": {
            "openai": settings.openai_api_key is not None,
            "google_places": settings.google_places_api_key is not None,
            "hubspot": settings.hubspot_access_token is not None,
        },
    }


@router.get("/analytics/summary", response_model=AnalyticsSummary)
def analytics_summary(db: Session = Depends(get_db)) -> AnalyticsSummary:
    return analytics.summary(db)
