from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Company, LeadStatus
from app.domain.normalize import normalize_name
from app.schemas import (
    CompanyDetail,
    CompanyIn,
    CompanyOut,
    EnrichmentOut,
    ImportReport,
    LeadPage,
    PossibleDuplicate,
    ScoreOut,
    StatusUpdate,
)
from app.services import leads as lead_service

router = APIRouter(prefix="/leads", tags=["leads"])


def _get_company(db: Session, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"lead {company_id} not found")
    return company


def to_detail(company: Company) -> CompanyDetail:
    detail = CompanyDetail.model_validate(company)
    if company.latest_enrichment:
        detail.enrichment = EnrichmentOut.model_validate(company.latest_enrichment)
    if company.latest_score:
        detail.latest_score = ScoreOut.model_validate(company.latest_score)
    return detail


@router.post("/import", response_model=ImportReport, status_code=status.HTTP_201_CREATED)
async def import_leads(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ImportReport:
    """Import leads from a UTF-8 CSV. Duplicates and invalid rows are reported, not inserted."""
    content = await file.read(lead_service.MAX_CSV_BYTES + 1)
    try:
        return lead_service.import_csv(db, content, file.filename)
    except lead_service.CsvImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("", response_model=ImportReport, status_code=status.HTTP_201_CREATED)
def create_leads(items: list[CompanyIn], db: Session = Depends(get_db)) -> ImportReport:
    if not items or len(items) > 500:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "send between 1 and 500 leads")
    return lead_service.ingest(db, enumerate(items, start=1), source="api", label="api")


@router.get("", response_model=LeadPage)
def list_leads(
    db: Session = Depends(get_db),
    country: str | None = Query(default=None, max_length=2),
    industry: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    qualification: str | None = None,
    min_score: int | None = Query(default=None, ge=0, le=100),
    q: str | None = Query(default=None, max_length=100),
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LeadPage:
    stmt = select(Company)
    if not include_archived:
        stmt = stmt.where(Company.status != LeadStatus.ARCHIVED)
    if country:
        stmt = stmt.where(Company.country == country.upper())
    if industry:
        stmt = stmt.where(Company.industry == industry)
    if status_:
        stmt = stmt.where(Company.status == status_)
    if qualification:
        stmt = stmt.where(Company.qualification_status == qualification)
    if min_score is not None:
        stmt = stmt.where(Company.score >= min_score)
    if q:
        pattern = f"%{normalize_name(q)}%"
        stmt = stmt.where(
            or_(Company.normalized_name.like(pattern), Company.domain.like(f"%{q.lower()}%"))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Company.score.desc().nulls_last(), Company.id).limit(limit).offset(offset)
    )
    return LeadPage(total=total, items=[CompanyOut.model_validate(c) for c in rows])


@router.get("/duplicates", response_model=list[PossibleDuplicate])
def possible_duplicates(
    threshold: float = Query(default=0.85, ge=0.5, le=1.0), db: Session = Depends(get_db)
) -> list[PossibleDuplicate]:
    """Near-duplicate pairs among stored leads, for manual review."""
    return lead_service.find_possible_duplicates(db, threshold)


@router.get("/{company_id}", response_model=CompanyDetail)
def get_lead(company_id: int, db: Session = Depends(get_db)) -> CompanyDetail:
    return to_detail(_get_company(db, company_id))


@router.patch("/{company_id}/status", response_model=CompanyOut)
def update_status(company_id: int, body: StatusUpdate, db: Session = Depends(get_db)) -> CompanyOut:
    company = _get_company(db, company_id)
    company.status = body.status
    if body.status in ("qualified", "disqualified", "needs_review"):
        company.qualification_status = body.status  # manual override by a human reviewer
    db.commit()
    return CompanyOut.model_validate(company)
