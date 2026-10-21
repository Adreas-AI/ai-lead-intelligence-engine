"""Enrichment, scoring, outreach and full-pipeline endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_llm
from app.api.routes.leads import _get_company
from app.core.config import Settings, get_settings
from app.db.models import OutreachDraft
from app.providers.llm import LLMProvider
from app.schemas import (
    BatchRequest,
    BatchResult,
    DraftOut,
    DraftReview,
    EnrichmentOut,
    OutreachRequest,
    PipelineRequest,
    PipelineResult,
    ScoreOut,
    ScoringRequest,
)
from app.services.enrichment import EnrichmentService
from app.services.outreach import OutreachService
from app.services.scoring import ScoringService

router = APIRouter(tags=["pipeline"])


def _require_llm(use_llm: bool, llm: LLMProvider | None) -> None:
    if use_llm and llm is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "use_llm=true but LLM_PROVIDER=none")


# --- Enrichment ---------------------------------------------------------------------
@router.post("/enrichment/run", response_model=BatchResult)
def run_enrichment(
    body: BatchRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BatchResult:
    return EnrichmentService(db, settings).run_batch(
        body.company_ids, limit=body.limit, force=body.force
    )


@router.post("/leads/{company_id}/enrich", response_model=EnrichmentOut)
def enrich_one(
    company_id: int,
    use_llm: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    llm: LLMProvider | None = Depends(get_llm),
) -> EnrichmentOut:
    _require_llm(use_llm, llm)
    company = _get_company(db, company_id)
    record = EnrichmentService(db, settings, llm=llm).enrich(company, use_llm=use_llm)
    db.commit()
    return EnrichmentOut.model_validate(record)


# --- Scoring ------------------------------------------------------------------------
@router.post("/scoring/run", response_model=BatchResult)
def run_scoring(
    body: ScoringRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    llm: LLMProvider | None = Depends(get_llm),
) -> BatchResult:
    _require_llm(body.use_llm, llm)
    return ScoringService(db, settings, llm).run_batch(
        body.company_ids, limit=body.limit, force=body.force, use_llm=body.use_llm
    )


@router.post("/leads/{company_id}/score", response_model=ScoreOut)
def score_one(
    company_id: int,
    use_llm: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    llm: LLMProvider | None = Depends(get_llm),
) -> ScoreOut:
    """Score one lead and return the structured, explainable result."""
    _require_llm(use_llm, llm)
    company = _get_company(db, company_id)
    record = ScoringService(db, settings, llm).score(company, use_llm=use_llm)
    db.commit()
    return ScoreOut.model_validate(record)


# --- Outreach -----------------------------------------------------------------------
@router.post("/outreach/generate", response_model=BatchResult)
def generate_outreach(
    body: OutreachRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    llm: LLMProvider | None = Depends(get_llm),
) -> BatchResult:
    """Create drafts for qualified leads. Drafts await human review; nothing is sent."""
    _require_llm(body.use_llm, llm)
    return OutreachService(db, settings, llm).run_batch(
        body.company_ids,
        limit=body.limit,
        force=body.force,
        use_llm=body.use_llm,
        include_needs_review=body.include_needs_review,
    )


@router.get("/outreach/drafts", response_model=list[DraftOut])
def list_drafts(
    status_: str | None = Query(default=None, alias="status"),
    company_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[DraftOut]:
    stmt = select(OutreachDraft).order_by(OutreachDraft.id.desc()).limit(limit)
    if status_:
        stmt = stmt.where(OutreachDraft.status == status_)
    if company_id:
        stmt = stmt.where(OutreachDraft.company_id == company_id)
    return [DraftOut.model_validate(d) for d in db.scalars(stmt)]


@router.patch("/outreach/drafts/{draft_id}", response_model=DraftOut)
def review_draft(draft_id: int, body: DraftReview, db: Session = Depends(get_db)) -> DraftOut:
    draft = db.get(OutreachDraft, draft_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"draft {draft_id} not found")
    draft.status = body.status
    draft.reviewer_notes = body.reviewer_notes
    if body.subject:
        draft.subject = body.subject
    if body.body:
        draft.body = body.body
    draft.reviewed_at = datetime.now(UTC)
    db.commit()
    return DraftOut.model_validate(draft)


# --- Full pipeline ------------------------------------------------------------------
@router.post("/pipeline/run", response_model=PipelineResult)
def run_pipeline(
    body: PipelineRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    llm: LLMProvider | None = Depends(get_llm),
) -> PipelineResult:
    """Enrich -> score -> draft every lead that has not been through each stage yet."""
    _require_llm(body.use_llm, llm)
    enrichment = scoring = outreach = None
    if body.enrich:
        enrichment = EnrichmentService(db, settings, llm=llm).run_batch(
            limit=body.limit, use_llm=body.use_llm
        )
    if body.score:
        scoring = ScoringService(db, settings, llm).run_batch(
            limit=body.limit, use_llm=body.use_llm
        )
    if body.draft:
        outreach = OutreachService(db, settings, llm).run_batch(
            limit=body.limit, use_llm=body.use_llm
        )
    return PipelineResult(enrichment=enrichment, scoring=scoring, outreach=outreach)
