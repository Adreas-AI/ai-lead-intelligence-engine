"""Lead ingestion (CSV / discovery / API) with duplicate detection.

Duplicate rules, in order of confidence:
1. same official domain                                -> duplicate
2. same normalised legal name in the same country      -> duplicate
3. fuzzy name similarity >= threshold, same country    -> duplicate
Matched records are *not* overwritten; blank fields on the existing record are
filled in from the new row so no information is lost.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from pydantic import ValidationError
from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Company, ImportBatch
from app.domain.normalize import extract_domain, name_similarity, normalize_name
from app.domain.profiles import Profile, get_profile
from app.schemas import CompanyIn, DuplicateMatch, ImportReport, PossibleDuplicate, RowError

log = get_logger(__name__)

FUZZY_THRESHOLD = 0.9
MAX_CSV_BYTES = 5_000_000
MAX_CSV_ROWS = 10_000

HEADER_ALIASES = {
    "company": "name",
    "company_name": "name",
    "companyname": "name",
    "organisation": "name",
    "organization": "name",
    "url": "website",
    "website_url": "website",
    "domain": "website",
    "country_code": "country",
    "type": "company_type",
    "business_type": "company_type",
    "sector": "industry",
    "tags": "keywords",
    "notes": "description",
    "source_link": "source_url",
}


class CsvImportError(ValueError):
    pass


def find_duplicate(db: Session, item: CompanyIn) -> tuple[Company, str, float] | None:
    domain = extract_domain(item.website)
    if domain:
        existing = db.scalar(select(Company).where(Company.domain == domain))
        if existing:
            return existing, "same_domain", 1.0

    norm = normalize_name(item.name)
    if not norm:
        return None
    country_filter = (
        or_(Company.country == item.country, Company.country.is_(None)) if item.country else true()
    )
    exact = db.scalar(
        select(Company).where(Company.normalized_name == norm).where(country_filter).limit(1)
    )
    if exact:
        return exact, "same_name_and_country", 1.0

    first_token = norm.split()[0]
    candidates = db.scalars(
        select(Company)
        .where(Company.normalized_name.like(f"{first_token}%"))
        .where(country_filter)
        .limit(200)
    )
    best: tuple[Company, float] | None = None
    for candidate in candidates:
        sim = name_similarity(item.name, candidate.name)
        if sim >= FUZZY_THRESHOLD and (best is None or sim > best[1]):
            best = (candidate, sim)
    if best:
        return best[0], "similar_name_same_country", round(best[1], 3)
    return None


def _fill_blanks(existing: Company, item: CompanyIn, profile: Profile) -> None:
    if not existing.website and item.website:
        domain = extract_domain(item.website)
        existing.website = item.website
        existing.domain = existing.domain or domain
    for attr in ("country", "city", "description", "source_url"):
        if getattr(existing, attr) in (None, "") and getattr(item, attr):
            setattr(existing, attr, getattr(item, attr))
    if existing.profile != profile.name:
        return  # a lead keeps its own profile; only neutral facts are merged
    if existing.industry in ("unknown", "other") and item.industry:
        existing.industry = profile.classify_industry(item.industry)
        existing.industry_raw = item.industry
    if existing.company_type == "unknown" and item.company_type:
        existing.company_type = profile.classify_business_type(item.company_type)
    if item.keywords:
        existing.keywords = sorted(set(existing.keywords or []) | set(item.keywords))


def create_company(db: Session, item: CompanyIn, profile: Profile | None = None) -> Company:
    profile = profile or get_profile(item.profile)
    company = Company(
        name=item.name,
        normalized_name=normalize_name(item.name),
        website=item.website,
        domain=extract_domain(item.website),
        country=item.country,
        city=item.city,
        industry=profile.classify_industry(item.industry),
        industry_raw=item.industry,
        company_type=profile.classify_business_type(item.company_type),
        profile=profile.name,
        description=item.description,
        keywords=item.keywords,
        source=item.source,
        source_url=item.source_url,
        source_ref=item.source_ref,
        is_synthetic=item.is_synthetic,
    )
    db.add(company)
    db.flush()
    return company


def ingest(
    db: Session,
    rows: Iterable[tuple[int | None, CompanyIn]],
    *,
    source: str,
    profile: str | None = None,
    label: str | None = None,
    invalid: list[RowError] | None = None,
    total_rows: int | None = None,
) -> ImportReport:
    """Store new leads, report duplicates. Raises ProfileError for an unknown profile."""
    batch_profile = get_profile(profile)
    invalid = invalid or []
    duplicates: list[DuplicateMatch] = []
    created_ids: list[int] = []
    count = 0
    for row_no, item in rows:
        count += 1
        match = find_duplicate(db, item)
        if match:
            existing, reason, sim = match
            _fill_blanks(existing, item, get_profile(item.profile or batch_profile.name))
            duplicates.append(
                DuplicateMatch(
                    row=row_no,
                    name=item.name,
                    matched_company_id=existing.id,
                    matched_name=existing.name,
                    reason=reason,
                    similarity=sim,
                )
            )
            continue
        item_profile = get_profile(item.profile) if item.profile else batch_profile
        created_ids.append(create_company(db, item, item_profile).id)

    batch = ImportBatch(
        source=source,
        profile=batch_profile.name,
        label=label,
        total_rows=total_rows if total_rows is not None else count + len(invalid),
        created_count=len(created_ids),
        duplicate_count=len(duplicates),
        invalid_count=len(invalid),
        details={
            "duplicates": [d.model_dump() for d in duplicates],
            "invalid": [e.model_dump() for e in invalid],
        },
    )
    db.add(batch)
    db.commit()
    log.info(
        "leads_ingested",
        source=source,
        created=len(created_ids),
        duplicates=len(duplicates),
        invalid=len(invalid),
    )
    return ImportReport(
        batch_id=batch.id,
        source=source,
        profile=batch_profile.name,
        total_rows=batch.total_rows,
        created=len(created_ids),
        duplicates=duplicates,
        invalid=invalid,
        created_ids=created_ids,
    )


def parse_csv(
    content: bytes, *, default_source: str = "csv"
) -> tuple[list[tuple[int, CompanyIn]], list[RowError], int]:
    if len(content) > MAX_CSV_BYTES:
        raise CsvImportError(f"CSV larger than {MAX_CSV_BYTES // 1_000_000} MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvImportError("CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CsvImportError("CSV has no header row")
    headers = {
        h: HEADER_ALIASES.get(
            h.strip().lower().replace(" ", "_"), h.strip().lower().replace(" ", "_")
        )
        for h in reader.fieldnames
    }
    if "name" not in headers.values():
        raise CsvImportError("CSV needs a 'name' (or 'company') column")

    valid: list[tuple[int, CompanyIn]] = []
    errors: list[RowError] = []
    total = 0
    for row_no, raw in enumerate(reader, start=2):  # row 1 is the header
        total += 1
        if total > MAX_CSV_ROWS:
            raise CsvImportError(f"CSV exceeds {MAX_CSV_ROWS} rows")
        data = {headers[k]: (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        data = {k: v for k, v in data.items() if v not in (None, "")}
        if "is_synthetic" in data:
            data["is_synthetic"] = str(data["is_synthetic"]).lower() in ("1", "true", "yes")
        data.setdefault("source", default_source)
        try:
            valid.append((row_no, CompanyIn(**data)))
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            errors.append(RowError(row=row_no, error=msg))
    return valid, errors, total


def import_csv(
    db: Session, content: bytes, filename: str | None = None, profile: str | None = None
) -> ImportReport:
    get_profile(profile)  # fail fast on an unknown profile, before parsing
    valid, errors, total = parse_csv(content)
    return ingest(
        db, valid, source="csv", profile=profile, label=filename, invalid=errors, total_rows=total
    )


def find_possible_duplicates(
    db: Session, threshold: float = 0.85, limit: int = 100
) -> list[PossibleDuplicate]:
    """Scan stored leads for near-duplicates that slipped past import (for human review)."""
    companies = list(db.scalars(select(Company).order_by(Company.id)))
    by_country: dict[str | None, list[Company]] = {}
    for c in companies:
        by_country.setdefault(c.country, []).append(c)
    pairs: list[PossibleDuplicate] = []
    for country, group in by_country.items():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                sim = name_similarity(a.name, b.name)
                if sim >= threshold:
                    pairs.append(
                        PossibleDuplicate(
                            company_id=a.id,
                            company_name=a.name,
                            other_id=b.id,
                            other_name=b.name,
                            country=country,
                            similarity=round(sim, 3),
                        )
                    )
    pairs.sort(key=lambda p: p.similarity, reverse=True)
    return pairs[:limit]
