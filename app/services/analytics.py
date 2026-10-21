from __future__ import annotations

from sqlalchemy import case, func, literal_column, select
from sqlalchemy.orm import Session

from app.db.models import (
    Company,
    CrmSyncRecord,
    DraftStatus,
    Enrichment,
    ImportBatch,
    LeadStatus,
    OutreachDraft,
    QualificationStatus,
    SyncStatus,
    UsageEvent,
)
from app.schemas import AnalyticsSummary, CostItem, CountItem


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _group(db: Session, column, base) -> list[CountItem]:
    key = func.coalesce(column, literal_column("'unknown'")).label("key")
    rows = db.execute(
        select(key, func.count().label("n"))
        .select_from(base)
        .where(Company.status != LeadStatus.ARCHIVED)
        .group_by("key")
        .order_by(literal_column("n").desc())
    ).all()
    return [CountItem(key=str(k), count=c) for k, c in rows]


def summary(db: Session) -> AnalyticsSummary:
    active = Company.status != LeadStatus.ARCHIVED
    total = db.scalar(select(func.count()).select_from(Company).where(active)) or 0
    enriched = (
        db.scalar(
            select(func.count(func.distinct(Enrichment.company_id)))
            .join(Company)
            .where(active, Enrichment.status.in_(["success", "partial"]))
        )
        or 0
    )
    scored = (
        db.scalar(
            select(func.count()).select_from(Company).where(active, Company.score.is_not(None))
        )
        or 0
    )

    def by_q(status: QualificationStatus) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(Company)
                .where(active, Company.qualification_status == status.value)
            )
            or 0
        )

    qualified = by_q(QualificationStatus.QUALIFIED)
    drafts = db.scalar(select(func.count(func.distinct(OutreachDraft.company_id)))) or 0
    approved = (
        db.scalar(
            select(func.count())
            .select_from(OutreachDraft)
            .where(OutreachDraft.status == DraftStatus.APPROVED)
        )
        or 0
    )
    synced = (
        db.scalar(
            select(func.count())
            .select_from(CrmSyncRecord)
            .where(CrmSyncRecord.provider != "csv", CrmSyncRecord.status == SyncStatus.SUCCESS)
        )
        or 0
    )
    crm_failures = (
        db.scalar(
            select(func.count())
            .select_from(CrmSyncRecord)
            .where(CrmSyncRecord.status == SyncStatus.FAILED)
        )
        or 0
    )
    duplicates = db.scalar(select(func.coalesce(func.sum(ImportBatch.duplicate_count), 0))) or 0
    avg = db.scalar(select(func.avg(Company.score)).where(active, Company.score.is_not(None)))

    bucket = case(
        (Company.score < 30, "0-29"),
        (Company.score < 50, "30-49"),
        (Company.score < 70, "50-69"),
        (Company.score < 85, "70-84"),
        else_="85-100",
    ).label("bucket")
    dist_rows = db.execute(
        select(bucket, func.count()).where(active, Company.score.is_not(None)).group_by("bucket")
    ).all()
    order = ["0-29", "30-49", "50-69", "70-84", "85-100"]
    dist = {k: c for k, c in dist_rows}

    cost_rows = db.execute(
        select(
            UsageEvent.provider,
            UsageEvent.operation,
            func.sum(UsageEvent.request_count),
            func.sum(UsageEvent.prompt_tokens),
            func.sum(UsageEvent.completion_tokens),
            func.sum(UsageEvent.cost_usd),
        ).group_by(UsageEvent.provider, UsageEvent.operation)
    ).all()
    costs = [
        CostItem(
            provider=p,
            operation=o,
            requests=r or 0,
            prompt_tokens=pt or 0,
            completion_tokens=ct or 0,
            cost_usd=round(c or 0.0, 6),
        )
        for p, o, r, pt, ct, c in cost_rows
    ]

    return AnalyticsSummary(
        total_leads=total,
        enriched_leads=enriched,
        scored_leads=scored,
        qualified_leads=qualified,
        needs_review_leads=by_q(QualificationStatus.NEEDS_REVIEW),
        disqualified_leads=by_q(QualificationStatus.DISQUALIFIED),
        insufficient_data_leads=by_q(QualificationStatus.INSUFFICIENT_DATA),
        drafts_generated=drafts,
        drafts_approved=approved,
        crm_synced=synced,
        crm_failures=crm_failures,
        duplicates_blocked=int(duplicates),
        average_score=round(float(avg), 1) if avg is not None else None,
        conversion={
            "discovered_to_enriched": _ratio(enriched, total),
            "enriched_to_qualified": _ratio(qualified, enriched),
            "scored_to_qualified": _ratio(qualified, scored),
            "qualified_to_drafted": _ratio(drafts, qualified),
            "drafted_to_approved": _ratio(approved, drafts),
            "qualified_to_crm": _ratio(synced, qualified),
        },
        by_country=_group(db, Company.country, Company),
        by_industry=_group(db, Company.industry, Company),
        by_status=_group(db, Company.status, Company),
        by_source=_group(db, Company.source, Company),
        score_distribution=[CountItem(key=k, count=dist.get(k, 0)) for k in order],
        costs=costs,
        total_cost_usd=round(sum(c.cost_usd for c in costs), 6),
    )
