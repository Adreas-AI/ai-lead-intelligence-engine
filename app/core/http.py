"""Shared outbound HTTP client: identifies itself, rate-limits per host, retries."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiterRegistry
from app.core.retry import RetryPolicy, call_with_retry


class HttpClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        rate_per_second: float | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout: float | None = None,
    ):
        self.settings = settings or get_settings()
        self._client = httpx.Client(
            timeout=timeout or self.settings.http_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.settings.http_user_agent},
            transport=transport,
        )
        self.limiters = RateLimiterRegistry(
            rate_per_second or self.settings.outbound_requests_per_second
        )
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=self.settings.http_max_retries)

    def request(
        self, method: str, url: str, *, raise_for_status: bool = True, **kwargs: Any
    ) -> httpx.Response:
        host = urlsplit(url).hostname or "unknown"

        def _do() -> httpx.Response:
            self.limiters.get(host).acquire()
            response = self._client.request(method, url, **kwargs)
            if raise_for_status:
                response.raise_for_status()
            return response

        return call_with_retry(_do, self.retry_policy, operation=f"{method} {host}")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def close(self) -> None:
        self._client.close()
