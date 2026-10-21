import pytest

from app.db.models import Company, ImportBatch
from app.schemas import CompanyIn
from app.services.leads import (
    CsvImportError,
    find_possible_duplicates,
    import_csv,
    ingest,
    parse_csv,
)


def test_demo_csv_detects_duplicates_and_invalid_rows(db, demo_csv):
    report = import_csv(db, demo_csv, "agrifood_b2b.csv", profile="agrifood_b2b")
    assert report.created == 24
    reasons = {d.name: d.reason for d in report.duplicates}
    assert reasons == {
        "Aegean Citrus Growers Co-operative": "same_domain",
        "KALAMATA VALLEY OLIVE OIL SA": "same_name_and_country",
        "Thessaly Grain Trader": "similar_name_same_country",
    }
    assert {e.row for e in report.invalid} == {29, 30}
    assert db.query(Company).count() == 24
    batch = db.get(ImportBatch, report.batch_id)
    assert (batch.duplicate_count, batch.invalid_count, batch.total_rows) == (3, 2, 29)


def test_reimport_creates_nothing(db, demo_csv):
    import_csv(db, demo_csv, profile="agrifood_b2b")
    second = import_csv(db, demo_csv, profile="agrifood_b2b")
    assert second.created == 0
    assert len(second.duplicates) == 27


def test_duplicate_fills_blank_fields_without_overwriting(db):
    ingest(db, [(1, CompanyIn(name="Crete Avocado Growers", country="GR"))], source="api")
    ingest(
        db,
        [
            (
                1,
                CompanyIn(
                    name="Crete Avocado Growers",
                    country="GR",
                    city="Chania",
                    website="https://crete-avocado.example",
                ),
            )
        ],
        source="api",
    )
    company = db.query(Company).one()
    assert company.city == "Chania"
    assert company.domain == "crete-avocado.example"


def test_same_name_in_different_country_is_not_a_duplicate(db):
    report = ingest(
        db,
        [
            (1, CompanyIn(name="Fresh Fruit Co", country="GR")),
            (2, CompanyIn(name="Fresh Fruit Co", country="ES")),
        ],
        source="api",
    )
    assert report.created == 2


def test_header_aliases_and_keywords(db):
    content = "Company,URL,Country Code,Sector,Tags\nAtlas Spice Trading,atlas-spice.example,MA,Trading,saffron; paprika\n"
    valid, errors, total = parse_csv(content.encode())
    assert total == 1 and not errors
    item = valid[0][1]
    assert item.website == "https://atlas-spice.example"
    assert item.keywords == ["saffron", "paprika"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "no header"),
        (b"website\nhttps://a.example\n", "'name'"),
        ("name\nÄ\n".encode("utf-16"), "UTF-8"),
    ],
)
def test_bad_csv_files_are_rejected(content, message):
    with pytest.raises(CsvImportError, match=message):
        parse_csv(content)


def test_find_possible_duplicates(db):
    ingest(db, [(1, CompanyIn(name="Danube Honey Producers", country="BG"))], source="api")
    # Insert a near-duplicate directly, bypassing import-time dedup.
    from app.services.leads import create_company

    create_company(db, CompanyIn(name="Danube Honey Producer Union", country="BG"))
    db.commit()
    pairs = find_possible_duplicates(db, threshold=0.8)
    assert len(pairs) == 1 and pairs[0].country == "BG"


def test_leads_take_the_import_profile(db, demo_csv_for):
    from app.domain.profiles import ProfileError

    report = import_csv(db, demo_csv_for("hair_salons"), profile="hair_salons")
    assert report.profile == "hair_salons"
    companies = db.query(Company).all()
    assert {c.profile for c in companies} == {"hair_salons"}
    lumiere = next(c for c in companies if c.name.startswith("Lumi"))
    assert lumiere.industry == "hair_salon"
    with pytest.raises(ProfileError):
        import_csv(db, demo_csv_for("hair_salons"), profile="nope")


def test_default_profile_is_used(db):
    report = ingest(
        db, [(1, CompanyIn(name="Ouzeri Test", country="GR", industry="Taverna"))], source="api"
    )
    assert report.profile == "restaurants"
    assert db.query(Company).one().industry == "restaurant"
