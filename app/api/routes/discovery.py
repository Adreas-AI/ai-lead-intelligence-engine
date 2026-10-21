from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.providers.discovery.base import DiscoveryError, ProviderNotConfigured
from app.providers.discovery.registry import get_discovery_provider, list_discovery_providers
from app.schemas import DiscoveryRequest, DiscoveryResponse, ProviderInfo
from app.services.leads import ingest
from app.services.usage import record_usage

router = APIRouter(prefix="/discovery", tags=["discovery"])
log = get_logger(__name__)


@router.get("/providers", response_model=list[ProviderInfo])
def providers(settings: Settings = Depends(get_settings)) -> list[ProviderInfo]:
    return [
        ProviderInfo(
            name=p.name,
            description=p.description,
            requires_credentials=p.requires_credentials,
            configured=p.is_configured(),
        )
        for p in list_discovery_providers(settings)
    ]


@router.post("/search", response_model=DiscoveryResponse)
def search(
    body: DiscoveryRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DiscoveryResponse:
    """Search a discovery provider; with ``save=true`` new results are stored (deduplicated)."""
    try:
        provider = get_discovery_provider(body.provider, settings)
        outcome = provider.search(body)
    except ProviderNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except DiscoveryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception as exc:
        log.exception("discovery_failed", provider=body.provider)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{body.provider} failed: {exc}") from exc

    if outcome.requests_made:
        record_usage(
            db,
            provider=provider.name,
            operation="discovery",
            requests=outcome.requests_made,
            cost_usd=outcome.cost_usd,
        )
    report = None
    if body.save and outcome.companies:
        report = ingest(
            db,
            enumerate(outcome.companies, start=1),
            source=provider.name,
            label=f"discovery:{provider.name}",
        )
    else:
        db.commit()
    return DiscoveryResponse(
        provider=provider.name,
        found=len(outcome.companies),
        results=outcome.companies,
        import_report=report,
    )
