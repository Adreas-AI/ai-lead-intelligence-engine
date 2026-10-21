"""Polite website fetching.

* Respects robots.txt (RFC 9309 semantics: 4xx -> allowed, 5xx/unreachable -> disallowed).
* Identifies itself with a descriptive User-Agent, rate-limits per host, retries
  transient errors.
* Never attempts to bypass anti-bot protection: challenge pages, 401/403 and
  persistent 429 responses are reported as ``FetchBlocked`` and the lead is
  marked accordingly.
* Synthetic ``*.example`` domains (RFC 2606) are served from local fixtures.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import Settings
from app.core.http import HttpClient
from app.core.logging import get_logger

log = get_logger(__name__)

CHALLENGE_MARKERS = (
    "cf-chl-",
    "challenge-platform",
    "attention required! | cloudflare",
    "g-recaptcha",
    "h-captcha",
    "please verify you are a human",
    "ddos protection by",
)


class FetchError(Exception):
    pass


class FetchBlocked(FetchError):
    """Access is not permitted (robots.txt, auth wall, anti-bot challenge)."""


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status_code: int
    html: str
    fetched_at: datetime


class PageFetcher(ABC):
    @abstractmethod
    def fetch(self, url: str) -> FetchedPage: ...


class FixtureFetcher(PageFetcher):
    """Serves synthetic pages from ``data/demo/websites/<domain>[__<path>].html``."""

    def __init__(self, directory: Path):
        self.directory = directory

    def _path_for(self, url: str) -> Path:
        parts = urlsplit(url)
        host = (parts.hostname or "").removeprefix("www.")
        path = parts.path.strip("/").replace("/", "_")
        name = f"{host}__{path}.html" if path else f"{host}.html"
        return self.directory / name

    def fetch(self, url: str) -> FetchedPage:
        path = self._path_for(url)
        if not path.exists():
            raise FetchError(f"no fixture for {url} (expected {path.name})")
        html = path.read_text(encoding="utf-8")
        return FetchedPage(url, url, 200, html, datetime.now(UTC))


class HttpPageFetcher(PageFetcher):
    def __init__(self, settings: Settings, http: HttpClient | None = None):
        self.settings = settings
        self.http = http or HttpClient(settings)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._blocked_origins: dict[str, str] = {}
        self._lock = threading.Lock()

    def _block(self, origin: str, reason: str) -> FetchBlocked:
        with self._lock:
            self._blocked_origins[origin] = reason
        return FetchBlocked(reason)

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """Returns a parser, or None meaning 'everything allowed'. Raises FetchBlocked."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if origin in self._blocked_origins:
                raise FetchBlocked(self._blocked_origins[origin])
            if origin in self._robots:
                return self._robots[origin]
        try:
            response = self.http.get(urljoin(origin, "/robots.txt"), raise_for_status=False)
        except httpx.HTTPError as exc:
            log.info("robots_unreachable", origin=origin, error=str(exc))
            raise self._block(origin, f"robots.txt unreachable for {origin}; not crawling") from exc

        parser: RobotFileParser | None
        if 200 <= response.status_code < 300:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
        elif 400 <= response.status_code < 500:
            parser = None
        else:
            raise self._block(origin, f"robots.txt returned {response.status_code} for {origin}")
        with self._lock:
            self._robots[origin] = parser
        return parser

    def fetch(self, url: str) -> FetchedPage:
        parser = self._robots_for(url)
        if parser is not None and not parser.can_fetch(self.settings.http_user_agent, url):
            raise FetchBlocked(f"robots.txt disallows {url}")
        try:
            response = self.http.get(url, raise_for_status=False)
        except httpx.HTTPError as exc:
            raise FetchError(f"request failed for {url}: {exc}") from exc

        if response.status_code in (401, 403, 429):
            raise FetchBlocked(f"{url} returned HTTP {response.status_code}; not bypassing")
        if response.status_code >= 400:
            raise FetchError(f"{url} returned HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            raise FetchError(f"{url} is not HTML ({content_type or 'no content-type'})")

        html = response.text[: self.settings.max_page_bytes]
        lowered = html[:20000].lower()
        if any(marker in lowered for marker in CHALLENGE_MARKERS):
            raise FetchBlocked(f"{url} served an anti-bot challenge; not bypassing")
        return FetchedPage(url, str(response.url), response.status_code, html, datetime.now(UTC))


class RoutingFetcher(PageFetcher):
    """Uses fixtures for synthetic domains (or everything in offline mode)."""

    def __init__(self, settings: Settings, live: PageFetcher | None = None):
        self.settings = settings
        self.fixtures = FixtureFetcher(settings.demo_data_dir / "websites")
        self._live = live

    @property
    def live(self) -> PageFetcher:
        if self._live is None:
            self._live = HttpPageFetcher(self.settings)
        return self._live

    def fetch(self, url: str) -> FetchedPage:
        host = urlsplit(url).hostname or ""
        if host.endswith(".example") or self.settings.enrichment_mode == "offline":
            return self.fixtures.fetch(url)
        return self.live.fetch(url)
