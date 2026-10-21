"""SQLAlchemy ORM models.

A *lead* is a ``Company``, evaluated against one Ideal Customer Profile
(``profile``). Every pipeline stage appends its own record
(enrichment, score, draft, CRM sync) so history and provenance are preserved,
while the latest score/status is denormalised onto ``Company`` for fast filtering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class LeadStatus(StrEnum):
    NEW = "new"
    ENRICHED = "enriched"
    ENRICHMENT_FAILED = "enrichment_failed"
    QUALIFIED = "qualified"
    NEEDS_REVIEW = "needs_review"
    DISQUALIFIED = "disqualified"
    INSUFFICIENT_DATA = "insufficient_data"
    OUTREACH_DRAFTED = "outreach_drafted"
    SYNCED_TO_CRM = "synced_to_crm"
    CONTACTED = "contacted"
    ARCHIVED = "archived"


class QualificationStatus(StrEnum):
    QUALIFIED = "qualified"
    NEEDS_REVIEW = "needs_review"
    DISQUALIFIED = "disqualified"
    INSUFFICIENT_DATA = "insufficient_data"


class EnrichmentStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"  # robots.txt disallow or anti-bot response: we do not bypass


class DraftStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class SyncStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    website: Mapped[str | None] = mapped_column(String(512))
    domain: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    city: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    industry_raw: Mapped[str | None] = mapped_column(String(255))
    company_type: Mapped[str] = mapped_column(String(64), default="unknown")
    description: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[Any]] = mapped_column(default=list)

    source: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    source_ref: Mapped[str | None] = mapped_column(String(255))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)

    profile: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default=LeadStatus.NEW, index=True)
    score: Mapped[int | None] = mapped_column(Integer, index=True)
    qualification_status: Mapped[str | None] = mapped_column(String(32))
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    enrichments: Mapped[list[Enrichment]] = relationship(
        back_populates="company", cascade="all, delete-orphan", order_by="Enrichment.id"
    )
    scores: Mapped[list[LeadScore]] = relationship(
        back_populates="company", cascade="all, delete-orphan", order_by="LeadScore.id"
    )
    drafts: Mapped[list[OutreachDraft]] = relationship(
        back_populates="company", cascade="all, delete-orphan", order_by="OutreachDraft.id"
    )
    crm_syncs: Mapped[list[CrmSyncRecord]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_companies_name_country", "normalized_name", "country"),)

    @property
    def latest_enrichment(self) -> Enrichment | None:
        return self.enrichments[-1] if self.enrichments else None

    @property
    def latest_score(self) -> LeadScore | None:
        return self.scores[-1] if self.scores else None


class Enrichment(Base):
    __tablename__ = "enrichments"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    website_url: Mapped[str | None] = mapped_column(String(512))
    website_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    page_title: Mapped[str | None] = mapped_column(String(512))
    meta_description: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_method: Mapped[str | None] = mapped_column(String(32))
    products: Mapped[list[Any]] = mapped_column(default=list)
    categories: Mapped[list[Any]] = mapped_column(default=list)
    business_signals: Mapped[list[Any]] = mapped_column(default=list)
    negative_signals: Mapped[list[Any]] = mapped_column(default=list)
    inferred_company_type: Mapped[str | None] = mapped_column(String(64))
    detected_country: Mapped[str | None] = mapped_column(String(2))
    country_evidence: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    markets_mentioned: Mapped[list[Any]] = mapped_column(default=list)
    source_urls: Mapped[list[Any]] = mapped_column(default=list)
    missing_fields: Mapped[list[Any]] = mapped_column(default=list)
    error: Mapped[str | None] = mapped_column(Text)
    enriched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    company: Mapped[Company] = relationship(back_populates="enrichments")


class LeadScore(Base):
    __tablename__ = "lead_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    profile: Mapped[str] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer)
    rule_score: Mapped[int] = mapped_column(Integer)
    llm_adjustment: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    breakdown: Mapped[list[Any]] = mapped_column(default=list)
    reasoning: Mapped[str] = mapped_column(Text)
    llm_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    scoring_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    company: Mapped[Company] = relationship(back_populates="scores")


class OutreachDraft(Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    generation_method: Mapped[str] = mapped_column(String(32))
    facts_used: Mapped[dict[str, Any]] = mapped_column(default=dict)
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(32), default=DraftStatus.PENDING_REVIEW)
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="drafts")


class CrmSyncRecord(Base):
    __tablename__ = "crm_sync_records"
    __table_args__ = (UniqueConstraint("company_id", "provider", name="uq_crm_company_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="crm_syncs")


class ImportBatch(Base):
    """One CSV import or discovery run, with dedup statistics."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(255))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageEvent(Base):
    """Metered external call (LLM tokens or paid API request) for cost reporting."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=1)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
