from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import Settings, get_settings
from app.db.models import CrmSyncRecord
from app.providers.crm import CrmError, HubSpotProvider
from app.schemas import BatchResult, CrmSyncOut, CrmSyncRequest, CsvExportRequest
from app.services.crm import CrmSyncService, export_csv

router = APIRouter(prefix="/crm", tags=["crm"])


@router.post("/export/csv", response_class=Response)
def export_qualified_csv(
    body: CsvExportRequest = CsvExportRequest(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Download qualified leads as CSV (also saved under EXPORT_DIR)."""
    text, count, path = export_csv(
        db, settings, include_needs_review=body.include_needs_review, only_new=body.only_new
    )
    filename = path.name if path else "qualified_leads.csv"
    return Response(
        content=text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Exported-Rows": str(count),
        },
    )


@router.post("/sync", response_model=BatchResult)
def sync_crm(
    body: CrmSyncRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BatchResult:
    """Upsert qualified leads into HubSpot (requires HUBSPOT_ACCESS_TOKEN)."""
    try:
        provider = HubSpotProvider(settings)
    except CrmError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return CrmSyncService(db, provider).run_batch(body.company_ids, limit=body.limit)


@router.get("/sync-records", response_model=list[CrmSyncOut])
def sync_records(
    status_: str | None = Query(default=None, alias="status"),
    provider: str | None = None,
    db: Session = Depends(get_db),
) -> list[CrmSyncOut]:
    stmt = select(CrmSyncRecord).order_by(CrmSyncRecord.last_attempt_at.desc()).limit(500)
    if status_:
        stmt = stmt.where(CrmSyncRecord.status == status_)
    if provider:
        stmt = stmt.where(CrmSyncRecord.provider == provider)
    return [CrmSyncOut.model_validate(r) for r in db.scalars(stmt)]
