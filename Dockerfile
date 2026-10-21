FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install ".[dashboard]"

COPY alembic.ini ./
COPY alembic ./alembic
COPY dashboard ./dashboard
COPY .streamlit ./.streamlit
COPY scripts ./scripts
COPY data ./data

RUN useradd --create-home --uid 1000 appuser && mkdir -p /app/exports && chown -R appuser /app
USER appuser

EXPOSE 8000 8501
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
