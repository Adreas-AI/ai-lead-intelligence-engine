"""Outreach draft preparation (human-in-the-loop; nothing is ever sent).

Drafts are built only from verified facts. LLM drafts pass through guardrails;
any violation falls back to the deterministic template and is recorded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Company, DraftStatus, LeadStatus, OutreachDraft, QualificationStatus
from app.domain.taxonomy import COUNTRY_NAMES
from app.providers.llm import LLMError, LLMProvider
from app.schemas import BatchResult
from app.services.usage import record_usage

log = get_logger(__name__)

SUPPLY_SIDE = {"grower", "cooperative", "packer", "exporter", "food_manufacturer"}
DEMAND_SIDE = {"wholesaler", "distributor", "importer", "trader"}
PLACEHOLDER = re.compile(r"\{[^}]*\}|\[[^\]]*\]|<[^>]+>|\bXX+\b|lorem ipsum", re.I)
DIGITS = re.compile(r"\d+(?:[.,]\d+)?")
BANNED_PHRASES = (
    "guarantee",
    "guaranteed",
    "increase your sales by",
    "% more",
    "as you know",
    "congratulations on",
    "i loved your",
    "i was impressed by your recent",
    "award-winning",
    "risk-free",
    "act now",
    "limited time",
)

OUTREACH_SYSTEM_PROMPT = """You write short, professional B2B introduction emails for an
agricultural marketplace. Hard rules:
- Use ONLY the facts provided. Never invent achievements, news, numbers, certifications,
  customers, people's names or personal compliments.
- No statistics or numbers unless they appear in the facts.
- Explain plainly why the marketplace may be commercially relevant to this company.
- 90-160 words, one clear call to action (a short call), no hype, no placeholders.
- Address the company team, not a named person.
Respond with JSON only: {"subject": string (max 80 chars), "body": string}"""


@dataclass
class DraftFacts:
    name: str
    country: str | None
    company_type: str
    categories: list[str]
    products: list[str]
    business_signals: list[str]
    markets_mentioned: list[str]
    summary: str | None
    website: str | None
    website_verified: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def collect_facts(company: Company) -> tuple[DraftFacts, list[str]]:
    e = company.latest_enrichment
    warnings: list[str] = []
    verified = bool(e and e.website_verified)
    use_site = e is not None and verified
    if e is not None and not verified:
        warnings.append("website not verified as official; website-derived facts excluded")
    facts = DraftFacts(
        name=company.name,
        country=COUNTRY_NAMES.get(company.country or "", None),
        company_type=company.company_type,
        categories=list(e.categories) if use_site else [],
        products=list(e.products)[:4] if use_site else [],
        business_signals=list(e.business_signals) if use_site else [],
        markets_mentioned=list(e.markets_mentioned)[:4] if use_site else [],
        summary=(e.summary if use_site else None) or company.description,
        website=company.website,
        website_verified=verified,
    )
    if not facts.categories:
        warnings.append("no verified product categories: low personalisation")
    return facts, warnings


def _focus(facts: DraftFacts) -> str:
    if facts.categories:
        cats = [c.lower() for c in facts.categories[:2]]
        return " and ".join(cats)
    return "your product range"


def template_draft(facts: DraftFacts, settings: Settings) -> tuple[str, str]:
    focus = _focus(facts)
    where = f" in {facts.country}" if facts.country else ""
    if facts.categories:
        products = ", ".join(facts.products[:3])
        detail = f" (including {products})" if products else ""
        opener = f"Your website describes {facts.name} as working with {focus}{detail}."
    else:
        opener = f"I came across {facts.name} while researching agri-food companies{where}."

    if facts.company_type in SUPPLY_SIDE:
        relevance = (
            f"For producers and exporters of {focus}, the platform can be a way to reach "
            "professional buyers beyond existing channels."
        )
    elif facts.company_type in DEMAND_SIDE:
        relevance = (
            f"For companies that buy and distribute {focus}, the platform can be a way to "
            "source directly from producers and compare offers in one place."
        )
    else:
        relevance = (
            f"If {focus} is part of your business, there may be a fit on either the buying "
            "or the selling side."
        )
    export_line = ""
    if "export" in facts.business_signals:
        export_line = " Since you mention export activity, cross-border buyers may be relevant."

    subject = f"B2B buyers for {focus} | {facts.name}"[:120]
    body = (
        f"Hello {facts.name} team,\n\n"
        f"{opener} I work with {settings.sender_company}, "
        f"{settings.sender_value_proposition}.\n\n"
        f"{relevance}{export_line}\n\n"
        "Would you be open to a short call to see whether it is relevant for you? "
        "If not, no problem at all, and I will not follow up repeatedly.\n\n"
        f"Best regards,\n{settings.sender_name}\n{settings.sender_company}"
    )
    return subject, body


def validate_draft(subject: str, body: str, facts: DraftFacts, settings: Settings) -> list[str]:
    """Return guardrail violations (empty list = acceptable)."""
    problems: list[str] = []
    text = f"{subject}\n{body}"
    if facts.name.lower() not in body.lower():
        problems.append("does not mention the company name")
    if PLACEHOLDER.search(text):
        problems.append("contains placeholder text")
    allowed = (
        json.dumps(facts.as_dict()) + settings.sender_value_proposition + settings.sender_company
    )
    invented = [n for n in DIGITS.findall(text) if n not in allowed]
    if invented:
        problems.append(f"contains numbers not present in the facts: {invented[:5]}")
    lowered = text.lower()
    hits = [p for p in BANNED_PHRASES if p in lowered]
    if hits:
        problems.append(f"contains disallowed claims: {hits}")
    for claim in ("organic", "certified", "globalgap", "iso", "brc", "ifs"):
        if re.search(rf"\b{claim}\b", lowered) and claim not in allowed.lower():
            problems.append(f"mentions '{claim}' which is not in the verified facts")
    if not (3 <= len(subject) <= 120):
        problems.append("subject length out of range")
    if not (150 <= len(body) <= 1800):
        problems.append("body length out of range")
    return problems


class OutreachService:
    def __init__(self, db: Session, settings: Settings, llm: LLMProvider | None = None):
        self.db = db
        self.settings = settings
        self.llm = llm

    def _llm_draft(self, facts: DraftFacts) -> tuple[str, str] | None:
        assert self.llm is not None
        payload = json.dumps(
            {
                "facts": facts.as_dict(),
                "sender": {
                    "name": self.settings.sender_name,
                    "company": self.settings.sender_company,
                    "value_proposition": self.settings.sender_value_proposition,
                },
            }
        )
        try:
            result = self.llm.complete_json(
                OUTREACH_SYSTEM_PROMPT, payload, operation="outreach_draft"
            )
        except LLMError as exc:
            record_usage(
                self.db,
                provider=self.llm.name,
                operation="outreach_draft",
                model=self.llm.model,
                success=False,
            )
            log.warning("llm_outreach_failed", company=facts.name, error=str(exc))
            return None
        record_usage(
            self.db,
            provider=self.llm.name,
            operation="outreach_draft",
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=self.llm.cost_usd(result),
        )
        subject, body = result.data.get("subject"), result.data.get("body")
        if not isinstance(subject, str) or not isinstance(body, str):
            return None
        return subject.strip(), body.strip()

    def generate(self, company: Company, *, use_llm: bool = False) -> OutreachDraft:
        facts, warnings = collect_facts(company)
        method = "template"
        subject, body = template_draft(facts, self.settings)
        if use_llm and self.llm is not None:
            llm_out = self._llm_draft(facts)
            if llm_out is None:
                warnings.append("LLM draft unavailable; used template")
            else:
                problems = validate_draft(*llm_out, facts, self.settings)
                if problems:
                    warnings.append("LLM draft rejected by guardrails: " + "; ".join(problems))
                else:
                    subject, body = llm_out
                    method = f"llm:{self.llm.name}"

        problems = validate_draft(subject, body, facts, self.settings)
        warnings.extend(f"guardrail: {p}" for p in problems)
        draft = OutreachDraft(
            company_id=company.id,
            subject=subject,
            body=body,
            generation_method=method,
            facts_used=facts.as_dict(),
            warnings=warnings,
            status=DraftStatus.PENDING_REVIEW,
        )
        self.db.add(draft)
        if company.status not in (LeadStatus.SYNCED_TO_CRM, LeadStatus.CONTACTED):
            company.status = LeadStatus.OUTREACH_DRAFTED
        self.db.flush()
        return draft

    def run_batch(
        self,
        company_ids: list[int] | None = None,
        *,
        limit: int = 50,
        force: bool = False,
        use_llm: bool = False,
        include_needs_review: bool = False,
    ) -> BatchResult:
        statuses = [QualificationStatus.QUALIFIED.value]
        if include_needs_review:
            statuses.append(QualificationStatus.NEEDS_REVIEW.value)
        stmt = select(Company).where(Company.qualification_status.in_(statuses))
        if company_ids:
            stmt = stmt.where(Company.id.in_(company_ids))
        if not force:
            stmt = stmt.where(~Company.drafts.any())
        companies = list(self.db.scalars(stmt.order_by(Company.score.desc()).limit(limit)))
        result = BatchResult(processed=0, succeeded=0, failed=0)
        for company in companies:
            result.processed += 1
            try:
                draft = self.generate(company, use_llm=use_llm)
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                log.exception("outreach_crashed", company_id=company.id)
                result.failed += 1
                result.details.append({"company_id": company.id, "error": str(exc)})
                continue
            result.succeeded += 1
            result.details.append(
                {
                    "company_id": company.id,
                    "draft_id": draft.id,
                    "method": draft.generation_method,
                    "warnings": draft.warnings,
                }
            )
        return result
