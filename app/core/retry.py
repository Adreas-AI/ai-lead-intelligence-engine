"""Retry with exponential backoff and full jitter.

Used for every outbound call (websites, discovery APIs, LLM, CRM). Retries only
transient failures: network errors, HTTP 429 and HTTP 5xx. Honors ``Retry-After``.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import ParamSpec, TypeVar

import httpx

from app.core.logging import get_logger

P = ParamSpec("P")
T = TypeVar("T")

log = get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableError(Exception):
    """Raise to signal that an operation failed transiently and may be retried."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 10.0
    jitter: bool = True

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return random.uniform(0, delay) if self.jitter else delay


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def is_retryable(exc: BaseException) -> tuple[bool, float | None]:
    if isinstance(exc, RetryableError):
        return True, exc.retry_after
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in RETRYABLE_STATUS, _retry_after_seconds(exc.response)
    if isinstance(exc, httpx.TransportError):  # timeouts, connection resets, DNS
        return True, None
    return False, None


def call_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    *,
    operation: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
    classify: Callable[[BaseException], tuple[bool, float | None]] = is_retryable,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            retryable, retry_after = classify(exc)
            if not retryable or attempt >= policy.max_attempts:
                raise
            delay = policy.delay_for(attempt, retry_after)
            log.warning(
                "retrying_after_error",
                operation=operation,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                delay_seconds=round(delay, 3),
                error=f"{type(exc).__name__}: {exc}",
            )
            sleep(delay)


def with_retry(
    policy: RetryPolicy = RetryPolicy(), operation: str | None = None
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator form of :func:`call_with_retry`."""

    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return call_with_retry(
                lambda: fn(*args, **kwargs), policy, operation=operation or fn.__qualname__
            )

        return wrapper

    return decorator
