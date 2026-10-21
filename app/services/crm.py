"""Exporting qualified leads: CSV files and (optionally) HubSpot.

Duplicate CRM records are prevented in two ways: a unique (company, provider)
sync record that stores the external ID, and a domain lookup in the CRM before
creating anything. Failures are recorded per lead with the error message and
attempt count so they can be retried.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Company, CrmSyncRecord, LeadStatus, QualificationStatus, SyncStatus
from app.domain.taxonomy import COUNTRY_NAMES
from app.providers.crm import CrmError, CrmProvider
from app.schemas import BatchResult

log = get_logger(__name__)

CSV_COLUMNS = [
    "company_id",
    "name",
    "website",
    "domain",
    "country",
    "city",
    "industry",
    "company_type",
    "score",
    "qualification_status",
    "confidence",
    "summary",
    "categories",
    "reasoning",
    "source",
    "source_url",
    "is_synthetic",
    "latest_draft_subject",
    "latest_draft_status",
]


def _qualified_query(include_needs_review: bool):
    statuses = [QualificationStatus.QUALIFIED.value]
    if include_needs_review:
        statuses.append(QualificationStatus.NEEDS_REVIEW.value)
    return (
        select(Company)
        .where(Company.qualification_status.in_(statuses))
        .where(Company.status != LeadStatus.ARCHIVED)
        .order_by(Company.score.desc(), Company.id)
    )


def _row(company: Company) -> dict:
    e, s = company.latest_enrichment, company.latest_score
    draft = company.drafts[-1] if company.drafts else None
    return {
        "company_id": company.id,
        "name": company.name,
        "website": company.website or "",
        "domain": company.domain or "",
        "country": company.country or "",
        "city": company.city or "",
        "industry": company.industry,
        "company_type": company.company_type,
        "score": company.score,
        "qualification_status": company.qualification_status,
        "confidence": s.confidence if s else "",
        "summary": (e.summary if e else None) or "",
        "categories": "; ".join(e.categories) if e else "",
        "reasoning": s.reasoning if s else "",
        "source": company.source,
        "source_url": company.source_url or "",
        "is_synthetic": company.is_synthetic,
        "latest_draft_subject": draft.subject if draft else "",
        "latest_draft_status": draft.status if draft else "",
    }


def export_csv(
    db: Session,
    settings: Settings,
    *,
    include_needs_review: bool = False,
    only_new: bool = False,
    write_file: bool = True,
) -> tuple[str, int, Path | None]:
    """Returns (csv_text, row_count, file_path)."""
    stmt = _qualified_query(include_needs_review)
    if only_new:
        stmt = stmt.where(~Company.crm_syncs.any(CrmSyncRecord.provider == "csv"))
    companies = list(db.scalars(stmt))

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    now = datetime.now(UTC)
    for company in companies:
        writer.writerow(_row(company))
        record = next((r for r in company.crm_syncs if r.provider == "csv"), None)
        if record is None:
            record = CrmSyncRecord(company_id=company.id, provider="csv", attempts=0)
            db.add(record)
        record.status, record.error = SyncStatus.SUCCESS, None
        record.attempts += 1
        record.last_attempt_at = record.last_success_at = now
    db.commit()

    text = buffer.getvalue()
    path = None
    if write_file:
        settings.export_dir.mkdir(parents=True, exist_ok=True)
        path = settings.export_dir / f"qualified_leads_{now:%Y%m%d_%H%M%S}.csv"
        path.write_text(text, encoding="utf-8")
    log.info("csv_export", rows=len(companies), path=str(path) if path else None)
    return text, len(companies), path


def crm_properties(company: Company) -> dict[str, str]:
    e, s = company.latest_enrichment, company.latest_score
    description_parts = []
    if e and e.summary:
        description_parts.append(e.summary)
    if s:
        description_parts.append(f"Lead score {s.score}/100 ({s.status}). {s.reasoning}")
    props = {
        "name": company.name,
        "domain": company.domain or "",
        "website": company.website or "",
        "country": COUNTRY_NAMES.get(company.country or "", company.country or ""),
        "city": company.city or "",
        "description": "\n\n".join(description_parts)[:5000],
        "lifecyclestage": "lead",
    }
    return {k: v for k, v in props.items() if v}


class CrmSyncService:
    def __init__(self, db: Session, provider: CrmProvider):
        self.db = db
        self.provider = provider

    def sync_company(self, company: Company) -> CrmSyncRecord:
        record = self.db.scalar(
            select(CrmSyncRecord).where(
                CrmSyncRecord.company_id == company.id,
                CrmSyncRecord.provider == self.provider.name,
            )
        )
        if record is None:
            record = CrmSyncRecord(company_id=company.id, provider=self.provider.name, attempts=0)
            self.db.add(record)
        record.attempts += 1
        record.last_attempt_at = datetime.now(UTC)
        props = crm_properties(company)
        try:
            external_id = record.external_id
            if external_id is None and company.domain:
                external_id = self.provider.find_by_domain(company.domain)
            if external_id:
                self.provider.update_company(external_id, props)
            else:
                external_id = self.provider.create_company(props)
        except CrmError as exc:
            record.status, record.error = SyncStatus.FAILED, str(exc)[:2000]
            log.warning("crm_sync_failed", company_id=company.id, error=record.error)
            return record
        record.external_id = external_id
        record.status, record.error = SyncStatus.SUCCESS, None
        record.last_success_at = record.last_attempt_at
        if company.status != LeadStatus.CONTACTED:
            company.status = LeadStatus.SYNCED_TO_CRM
        return record

    def run_batch(self, company_ids: list[int] | None = None, *, limit: int = 100) -> BatchResult:
        stmt = _qualified_query(include_needs_review=False)
        if company_ids:
            stmt = stmt.where(Company.id.in_(company_ids))
        companies = list(self.db.scalars(stmt.limit(limit)))
        result = BatchResult(processed=0, succeeded=0, failed=0)
        for company in companies:
            result.processed += 1
            record = self.sync_company(company)
            self.db.commit()
            ok = record.status == SyncStatus.SUCCESS
            result.succeeded += ok
            result.failed += not ok
            result.details.append(
                {
                    "company_id": company.id,
                    "status": record.status,
                    "external_id": record.external_id,
                    "error": record.error,
                }
            )
        return result
