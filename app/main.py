from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.deps import require_api_key
from app.api.routes import crm, discovery, leads, pipeline, search, system
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import FixedWindowLimiter

log = get_logger("app.http")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Describe the businesses you want in plain words (any language). The engine "
            "finds them, enriches them from their own websites, scores them against an "
            "Ideal Customer Profile with explanations, and drafts outreach for human "
            "review. Nothing is sent automatically."
        ),
    )
    limiter = FixedWindowLimiter(settings.api_rate_limit_per_minute, 60)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        client = request.client.host if request.client else "unknown"
        if request.url.path != "/health":
            allowed, remaining, reset = limiter.hit(client)
            if not allowed:
                structlog.contextvars.clear_contextvars()
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(int(reset) + 1), "X-Request-ID": request_id},
                )
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        log.info(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        response.headers["X-Request-ID"] = request_id
        return response

    protected = [Depends(require_api_key)]
    app.include_router(system.public_router)
    routers = (
        search.router,
        leads.router,
        discovery.router,
        pipeline.router,
        crm.router,
        system.router,
    )
    for router in routers:
        app.include_router(router, dependencies=protected)
    return app


app = create_app()
