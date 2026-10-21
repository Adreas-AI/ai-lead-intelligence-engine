"""Test configuration: in-memory SQLite (or TEST_DATABASE_URL), offline fixtures, mock LLM.

Environment is set before the app is imported so settings and the engine pick it up.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "sqlite://"))
os.environ["ENVIRONMENT"] = "test"
os.environ["ENRICHMENT_MODE"] = "offline"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["API_RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["LOG_LEVEL"] = "WARNING"
for secret in ("API_KEY", "OPENAI_API_KEY", "GOOGLE_PLACES_API_KEY", "HUBSPOT_ACCESS_TOKEN"):
    os.environ.pop(secret, None)

from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api import deps  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.models import Base  # noqa: E402

DEMO_CSV = Path(__file__).resolve().parents[1] / "data" / "demo" / "synthetic_companies.csv"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    s = get_settings().model_copy(update={"export_dir": tmp_path / "exports"})
    return s


@pytest.fixture()
def engine():
    url = os.environ["DATABASE_URL"]
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        eng = create_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    if eng.dialect.name == "postgresql":
        # Fail fast instead of hanging if a test leaked a session holding a lock.
        with eng.begin() as conn:
            conn.exec_driver_sql("SET lock_timeout = '10s'")
            Base.metadata.drop_all(conn)
    else:
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db(engine) -> Iterator[Session]:
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine, settings) -> Iterator[TestClient]:
    from app.main import create_app

    app = create_app()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def demo_csv() -> bytes:
    return DEMO_CSV.read_bytes()
