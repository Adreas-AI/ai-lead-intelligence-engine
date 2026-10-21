"""Company enrichment.

For each lead: resolve/verify the official website, fetch it politely, extract
evidence-backed facts and store them with source URLs and a timestamp. Missing
data is recorded in ``missing_fields`` rather than filled with guesses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Company, Enrichment, EnrichmentStatus, LeadStatus
from app.domain.normalize import extract_domain
from app.domain.profiles import Profile, ProfileError, get_profile
from app.providers.llm import LLMError, LLMProvider
from app.providers.web import FetchBlocked, FetchError, PageFetcher, RoutingFetcher
from app.schemas import BatchResult
from app.services.extraction import (
    PageFacts,
    detect_country,
    extract_profile,
    extractive_summary,
    name_matches_site,
    parse_page,
)
from app.services.usage import record_usage

log = get_logger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You summarise B2B companies for a sales research tool. Use ONLY the supplied website "
    "text. Do not add facts, numbers, certifications, customers or locations that are not "
    "explicitly stated. If the text does not describe the business, set summary to null and "
    'insufficient_information to true. Respond as JSON: {"summary": string|null, '
    '"insufficient_information": boolean}. Maximum 2 sentences.'
)


class EnrichmentService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        fetcher: PageFetcher | None = None,
        llm: LLMProvider | None = None,
    ):
        self.db = db
        self.settings = settings
        self.fetcher = fetcher or RoutingFetcher(settings)
        self.llm = llm

    # ------------------------------------------------------------------ helpers
    def _fetch_site(self, url: str) -> tuple[list[PageFacts], list[str]]:
        home = self.fetcher.fetch(url)
        pages = [parse_page(home.html, home.final_url)]
        urls = [home.final_url]
        # Profiles are usually written in English: prefer the site's own English version.
        en_url = pages[0].english_url
        if pages[0].lang not in (None, "en") and en_url and en_url != home.final_url:
            try:
                english = self.fetcher.fetch(en_url)
                pages.insert(0, parse_page(english.html, english.final_url))
                urls.insert(0, english.final_url)
            except FetchError as exc:
                log.info("english_version_skipped", url=en_url, reason=str(exc))
        for link in pages[0].about_links[:1]:
            try:
                about = self.fetcher.fetch(link)
            except FetchError as exc:
                log.info("about_page_skipped", url=link, reason=str(exc))
                continue
            pages.append(parse_page(about.html, about.final_url))
            urls.append(about.final_url)
        return pages, urls

    def _llm_summary(self, company: Company, text: str) -> str | None:
        if self.llm is None or not text.strip():
            return None
        payload = json.dumps({"company_name": company.name, "text": text[:6000]})
        try:
            result = self.llm.complete_json(
                SUMMARY_SYSTEM_PROMPT, payload, operation="company_summary"
            )
        except LLMError as exc:
            record_usage(
                self.db,
                provider=self.llm.name,
                operation="company_summary",
                model=self.llm.model,
                success=False,
            )
            log.warning("llm_summary_failed", company_id=company.id, error=str(exc))
            return None
        record_usage(
            self.db,
            provider=self.llm.name,
            operation="company_summary",
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=self.llm.cost_usd(result),
        )
        summary = result.data.get("summary")
        if result.data.get("insufficient_information") or not isinstance(summary, str):
            return None
        return summary.strip()[:600] or None

    def _resolve_website(self, company: Company) -> str | None:
        """Look up a missing website via Google Places (only if a key is configured).

        The candidate is accepted only if the listing name closely matches the lead
        name; it is then verified against the fetched page like any other website.
        """
        if self.settings.google_places_api_key is None or company.is_synthetic:
            return None
        from app.domain.normalize import name_similarity
        from app.providers.discovery.google_places import GooglePlacesDiscoveryProvider
        from app.schemas import DiscoveryRequest

        provider = GooglePlacesDiscoveryProvider(self.settings)
        query = DiscoveryRequest(
            provider="google_places",
            countries=[company.country] if company.country else [],
            keywords=[company.name],
            limit=3,
            require_website=True,
        )
        try:
            outcome = provider.search(query, self._profile_for(company))
        except Exception as exc:
            log.warning("website_resolution_failed", company_id=company.id, error=str(exc))
            return None
        record_usage(
            self.db,
            provider="google_places",
            operation="website_resolution",
            requests=outcome.requests_made,
            cost_usd=outcome.cost_usd,
        )
        for candidate in outcome.companies:
            if candidate.website and name_similarity(candidate.name, company.name) >= 0.8:
                return candidate.website
        return None

    def _profile_for(self, company: Company) -> Profile:
        try:
            return get_profile(company.profile)
        except ProfileError:
            log.warning(
                "profile_missing_using_default", company_id=company.id, profile=company.profile
            )
            return get_profile(None)

    # --------------------------------------------------------------------- main
    def enrich(self, company: Company, *, use_llm: bool = False) -> Enrichment:
        if not company.website:
            resolved = self._resolve_website(company)
            domain = extract_domain(resolved)
            taken = domain and self.db.scalar(
                select(Company.id).where(Company.domain == domain, Company.id != company.id)
            )
            if resolved and not taken:
                company.website, company.domain = resolved, domain
        record = Enrichment(company_id=company.id, website_url=company.website)
        source_urls = [u for u in [company.source_url] if u]
        pages: list[PageFacts] = []
        status = EnrichmentStatus.PARTIAL

        if company.website:
            try:
                pages, fetched_urls = self._fetch_site(company.website)
                source_urls.extend(fetched_urls)
            except FetchBlocked as exc:
                status, record.error = EnrichmentStatus.BLOCKED, str(exc)
            except FetchError as exc:
                status, record.error = EnrichmentStatus.FAILED, str(exc)
        else:
            record.error = "no website on record"

        home = pages[0] if pages else None
        website_text = " ".join(p.text for p in pages)
        # Source-provided description and keywords are verified provenance (source_url).
        evidence_text = " ".join(
            filter(None, [website_text, company.description, " ".join(company.keywords or [])])
        )
        profile = extract_profile(evidence_text, self._profile_for(company))

        if home:
            record.page_title = home.title
            record.meta_description = home.meta_description
            record.website_verified = name_matches_site(company.name, home)
            country, evidence = detect_country(home, company.website or "")
            record.detected_country, record.country_evidence = country, evidence
            locality = next((p.address_locality for p in pages if p.address_locality), None)
            country_name = next((p.address_country for p in pages if p.address_country), None)
            record.location = ", ".join(filter(None, [locality, country_name])) or None

            summary = self._llm_summary(company, website_text) if use_llm else None
            if summary:
                record.summary, record.summary_method = summary, "llm"
            else:
                for page in pages:
                    summary = extractive_summary(page)
                    if summary:
                        record.summary, record.summary_method = summary, "extractive"
                        break
        if not record.summary and company.description:
            record.summary, record.summary_method = company.description[:600], "source_description"

        record.products = profile.products
        record.categories = profile.categories
        record.business_signals = profile.business_signals
        record.negative_signals = profile.negative_signals
        record.inferred_company_type = profile.inferred_company_type
        record.markets_mentioned = profile.markets_mentioned
        record.source_urls = list(dict.fromkeys(source_urls))

        missing = []
        if not company.website:
            missing.append("website")
        elif not record.website_verified:
            missing.append("website_verification")
        if not record.summary:
            missing.append("summary")
        if not (company.country or record.detected_country):
            missing.append("country")
        if not record.categories:
            missing.append("offering_categories")
        if not record.business_signals:
            missing.append("business_signals")
        record.missing_fields = missing

        if status not in (EnrichmentStatus.BLOCKED, EnrichmentStatus.FAILED):
            complete = (
                home is not None
                and record.website_verified
                and record.summary
                and record.categories
            )
            status = EnrichmentStatus.SUCCESS if complete else EnrichmentStatus.PARTIAL
        # Even a blocked/failed fetch is useful if the source gave us something to go on.
        if status == EnrichmentStatus.FAILED and not (record.summary or record.categories):
            company.status = LeadStatus.ENRICHMENT_FAILED
        else:
            company.status = LeadStatus.ENRICHED
        record.status = status
        record.enriched_at = datetime.now(UTC)

        # Fill blanks on the lead only with evidence-backed values.
        if not company.country and record.detected_country:
            company.country = record.detected_country
        if company.company_type == "unknown" and profile.inferred_company_type:
            company.company_type = profile.inferred_company_type
        if company.website and not company.domain:
            company.domain = extract_domain(company.website)
        company.last_enriched_at = record.enriched_at

        self.db.add(record)
        self.db.flush()
        log.info(
            "company_enriched",
            company_id=company.id,
            status=status,
            categories=len(record.categories),
            missing=missing,
        )
        return record

    def run_batch(
        self,
        company_ids: list[int] | None = None,
        *,
        limit: int = 50,
        force: bool = False,
        use_llm: bool = False,
    ) -> BatchResult:
        stmt = select(Company).where(Company.status != LeadStatus.ARCHIVED)
        if company_ids:
            stmt = stmt.where(Company.id.in_(company_ids))
        if not force:
            stmt = stmt.where(Company.last_enriched_at.is_(None))
        companies = list(self.db.scalars(stmt.order_by(Company.id).limit(limit)))

        result = BatchResult(processed=0, succeeded=0, failed=0)
        for company in companies:
            result.processed += 1
            try:
                record = self.enrich(company, use_llm=use_llm)
                self.db.commit()
            except Exception as exc:  # one bad lead must not stop the batch
                self.db.rollback()
                log.exception("enrichment_crashed", company_id=company.id)
                result.failed += 1
                result.details.append({"company_id": company.id, "error": str(exc)})
                continue
            ok = record.status in (EnrichmentStatus.SUCCESS, EnrichmentStatus.PARTIAL)
            result.succeeded += ok
            result.failed += not ok
            result.details.append(
                {
                    "company_id": company.id,
                    "status": record.status,
                    "error": record.error,
                    "missing": record.missing_fields,
                }
            )
        return result
