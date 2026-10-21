"""Outreach draft preparation (human-in-the-loop; nothing is ever sent).

Drafts are built only from verified facts plus the pitch defined in the lead's
profile. LLM drafts pass through guardrails; any violation falls back to the
deterministic template and is recorded.
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
from app.domain.geo import COUNTRY_NAMES
from app.domain.profiles import Profile, ProfileError, get_profile
from app.providers.llm import LLMError, LLMProvider
from app.schemas import BatchResult
from app.services.usage import record_usage

log = get_logger(__name__)

PLACEHOLDER = re.compile(r"\{[^}]*\}|\[[^\]]*\]|<[^>]+>|\bXX+\b|lorem ipsum", re.I)
DIGITS = re.compile(r"\d+(?:[.,]\d+)?")
BANNED_PHRASES = (
    "guarantee", "guaranteed", "increase your sales by", "% more", "as you know",
    "congratulations on", "i loved your", "i was impressed by your recent", "award-winning",
    "risk-free", "act now", "limited time",
)  # fmt: skip
UNVERIFIABLE_CLAIMS = ("organic", "certified", "globalgap", "iso", "brc", "ifs", "michelin")

OUTREACH_SYSTEM_PROMPT = """You write short, professional cold emails from a seller to a
business. Hard rules:
- Use ONLY the facts provided. Never invent achievements, news, numbers, certifications,
  customers, people's names or personal compliments.
- No statistics or numbers unless they appear in the facts.
- Explain plainly why the seller's offer may be relevant to this business.
- 90-160 words, one clear call to action (a short call), no hype, no placeholders.
- Address the business's team, not a named person. Write in English.
Respond with JSON only: {"subject": string (max 80 chars), "body": string}"""


@dataclass
class DraftFacts:
    name: str
    country: str | None
    city: str | None
    business_type: str | None
    categories: list[str]
    products: list[str]
    business_signals: list[str]
    markets_mentioned: list[str]
    summary: str | None
    website: str | None
    website_verified: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def collect_facts(company: Company, profile: Profile) -> tuple[DraftFacts, list[str]]:
    e = company.latest_enrichment
    warnings: list[str] = []
    verified = bool(e and e.website_verified)
    use_site = e is not None and verified
    if e is not None and not verified:
        warnings.append("website not verified as official; website-derived facts excluded")
    btype = profile.type_info(company.company_type)
    facts = DraftFacts(
        name=company.name,
        country=COUNTRY_NAMES.get(company.country or ""),
        city=company.city,
        business_type=btype.label if btype else None,
        categories=list(e.categories) if use_site else [],
        products=list(e.products)[:4] if use_site else [],
        business_signals=list(e.business_signals) if use_site else [],
        markets_mentioned=list(e.markets_mentioned)[:4] if use_site else [],
        summary=(e.summary if use_site else None) or company.description,
        website=company.website,
        website_verified=verified,
    )
    if not facts.categories:
        warnings.append("no verified offering details: low personalisation")
    return facts, warnings


def _focus(facts: DraftFacts) -> str:
    if facts.categories:
        return " and ".join(c.lower() for c in facts.categories[:2])
    return "what you offer"


def _fill(template: str, facts: DraftFacts) -> str:
    return template.replace("{name}", facts.name).replace("{focus}", _focus(facts)).strip()


def template_draft(
    facts: DraftFacts, profile: Profile, settings: Settings, company_type: str | None = None
) -> tuple[str, str]:
    focus = _focus(facts)
    where = f" in {facts.city or facts.country}" if (facts.city or facts.country) else ""
    if facts.categories:
        products = ", ".join(facts.products[:3])
        detail = f" (including {products})" if products else ""
        opener = f"Your website mentions {focus}{detail}."
    else:
        opener = f"I came across {facts.name} while looking at {profile.label.lower()}{where}."

    o = profile.outreach
    relevance = _fill(o.relevance_by_type.get(company_type or "", o.relevance_default), facts)
    extra = "".join(
        line.rstrip() if line.startswith(" ") else " " + line.strip()
        for sig, line in o.signal_lines.items()
        if sig in facts.business_signals
    )
    subject = _fill(o.subject, facts)[:120]
    pitch = profile.value_proposition().strip()
    body = (
        f"Hello {facts.name} team,\n\n"
        f"{opener} I work with {settings.sender_company}, {pitch}.\n\n"
        f"{relevance}{extra}\n\n"
        "Would you be open to a short call to see whether it is relevant for you? "
        "If not, no problem at all, and I will not follow up repeatedly.\n\n"
        f"Best regards,\n{settings.sender_name}\n{settings.sender_company}"
    )
    return subject, body


def validate_draft(
    subject: str, body: str, facts: DraftFacts, profile: Profile, settings: Settings
) -> list[str]:
    """Return guardrail violations (empty list = acceptable)."""
    problems: list[str] = []
    text = f"{subject}\n{body}"
    if facts.name.lower() not in body.lower():
        problems.append("does not mention the company name")
    if PLACEHOLDER.search(text):
        problems.append("contains placeholder text")
    o = profile.outreach
    allowed = " ".join(  # facts plus the seller's own, user-written pitch
        [
            json.dumps(facts.as_dict(), ensure_ascii=False),
            profile.value_proposition(),
            settings.sender_company,
            settings.sender_name,
            o.subject,
            o.relevance_default,
            *o.relevance_by_type.values(),
            *o.signal_lines.values(),
        ]
    )
    invented = [n for n in DIGITS.findall(text) if n not in allowed]
    if invented:
        problems.append(f"contains numbers not present in the facts: {invented[:5]}")
    lowered = text.lower()
    hits = [p for p in BANNED_PHRASES if p in lowered]
    if hits:
        problems.append(f"contains disallowed claims: {hits}")
    for claim in UNVERIFIABLE_CLAIMS:
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

    def _llm_draft(self, facts: DraftFacts, profile: Profile) -> tuple[str, str] | None:
        assert self.llm is not None
        payload = json.dumps(
            {
                "facts": facts.as_dict(),
                "sender": {
                    "name": self.settings.sender_name,
                    "company": self.settings.sender_company,
                    "value_proposition": profile.value_proposition(),
                },
                "target_profile": profile.label,
            },
            ensure_ascii=False,
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
        try:
            profile = get_profile(company.profile)
        except ProfileError:
            profile = get_profile(None)
        facts, warnings = collect_facts(company, profile)
        method = "template"
        subject, body = template_draft(facts, profile, self.settings, company.company_type)
        if use_llm and self.llm is not None:
            llm_out = self._llm_draft(facts, profile)
            if llm_out is None:
                warnings.append("LLM draft unavailable; used template")
            else:
                problems = validate_draft(*llm_out, facts, profile, self.settings)
                if problems:
                    warnings.append("LLM draft rejected by guardrails: " + "; ".join(problems))
                else:
                    subject, body = llm_out
                    method = f"llm:{self.llm.name}"

        problems = validate_draft(subject, body, facts, profile, self.settings)
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
