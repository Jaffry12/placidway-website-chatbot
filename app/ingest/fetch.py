"""Polite HTTP fetcher: obeys robots.txt (including Crawl-delay), sends a clear User-Agent and
waits between requests."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .robots import RobotRules, parse_robots

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: int
    html: str
    fetched_at: str


class PoliteFetcher:
    def __init__(
        self,
        user_agent: str,
        delay_s: float = 1.0,
        timeout_s: float = 20.0,
        retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ):
        self.user_agent = user_agent
        self.delay_s = delay_s  # our own minimum; robots.txt Crawl-delay can only make it longer
        self.retries = retries
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._robots: dict[str, RobotRules] = {}
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def delay_for(self, url: str) -> float:
        """Seconds between requests: our delay, or the site's Crawl-delay if that is longer."""
        return max(self.delay_s, self._robots_for(url).crawl_delay or 0.0)

    def _wait_turn(self, delay_s: float | None = None) -> None:
        wait = (self.delay_s if delay_s is None else delay_s) - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _robots_for(self, url: str) -> RobotRules:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._wait_turn()
            try:
                resp = self._client.get(f"{origin}/robots.txt")
                # A missing robots.txt means everything is allowed; a server error means be careful.
                if resp.status_code >= 500:
                    raise FetchError(f"robots.txt unavailable ({resp.status_code}) for {origin}")
                rules = parse_robots(resp.text if resp.status_code == 200 else "", self.user_agent)
            except httpx.HTTPError as exc:
                raise FetchError(f"could not read robots.txt for {origin}: {exc}") from exc
            self._robots[origin] = rules
        return self._robots[origin]

    def fetch(self, url: str) -> FetchedPage:
        if not self._robots_for(url).allows(url):
            raise FetchError(f"robots.txt disallows {url}")

        delay = self.delay_for(url)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait_turn(delay)
            try:
                resp = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    return FetchedPage(
                        url=url,
                        final_url=str(resp.url),
                        status=resp.status_code,
                        html=resp.text,
                        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    )
                if resp.status_code < 500 and resp.status_code != 429:
                    raise FetchError(f"{url} returned HTTP {resp.status_code}")
                last_error = FetchError(f"{url} returned HTTP {resp.status_code}")
            backoff = 2 ** attempt
            log.warning("fetch failed for %s (%s), retrying in %ss", url, last_error, backoff)
            time.sleep(backoff)
        raise FetchError(f"giving up on {url}: {last_error}")
