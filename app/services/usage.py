from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import UsageEvent


def record_usage(
    db: Session,
    *,
    provider: str,
    operation: str,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    requests: int = 1,
    cost_usd: float = 0.0,
    success: bool = True,
) -> UsageEvent:
    event = UsageEvent(
        provider=provider,
        operation=operation,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_count=requests,
        cost_usd=cost_usd,
        success=success,
    )
    db.add(event)
    return event
