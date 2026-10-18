"""Download GitHub Trending pages with retry and date-scoped caching."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Callable

import requests

from .config import Settings

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Both the Trending ``?since=`` values and the ``period`` strings
# ``parse_trending_html`` accepts. Keep this in lockstep with parse.GAIN_FIELD.
WINDOWS = ("daily", "weekly", "monthly")
BASE = "https://github.com/trending"
# Polite gap between page downloads. Cache hits still sleep: a resumed run
# that only needs the tail of the list should not burst the remaining GETs.
DELAY_SECONDS = 0.35

#: Retrying a missing page can never succeed — almost always a bad language
#: slug in config.yaml — so fail it immediately instead of burning retries.
NOT_FOUND_STATUSES = (404, 410)


class FetchError(Exception):
    """Raised when a page cannot be downloaded after all retries."""


def page_key(window: str, language: str | None, day: str) -> str:
    slug = language or "all"
    return f"pages/{day}/trending_{window}_{slug}.html"


class TrendingFetcher:
    def __init__(
        self,
        session=None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ) -> None:
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep
        self.retries = retries

    def get(self, url: str, retries: int | None = None) -> str:
        attempts = retries if retries is not None else self.retries
        delay = 1.0
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=30,
                    headers={"User-Agent": USER_AGENT},
                )
                if response.status_code in NOT_FOUND_STATUSES:
                    raise FetchError(
                        f"GET {url} returned {response.status_code} (not retryable)"
                    )
                response.raise_for_status()
                response.encoding = "utf-8"
                return response.text
            except (requests.RequestException, OSError) as exc:
                # Deliberately narrow: only transport/HTTP trouble is retried.
                # Anything else (a TypeError from a bad call, say) is a
                # programming error and must surface immediately rather than be
                # retried three times and rebranded as a network failure.
                last = exc
                log.warning(
                    "attempt %d/%d failed for %s: %s: %s",
                    attempt, attempts, url, type(exc).__name__, exc,
                )
                if attempt < attempts:
                    self.sleep(delay)
                    delay *= 2
        raise FetchError(
            f"GET {url} failed after {attempts} attempts: "
            f"{type(last).__name__}: {last}"
        ) from last

    def trending_urls(self, settings: Settings) -> Iterator[tuple[str, str | None, str]]:
        """Yield ``(window, language, url)`` for the global page then each language."""
        for window in WINDOWS:
            yield window, None, f"{BASE}?since={window}"
            for language in settings.languages:
                yield window, language, f"{BASE}/{language}?since={window}"

    def fetch_all(self, settings: Settings, cache, day: str) -> list[str]:
        """Fetch (or reuse cached) pages; returns the cache keys written/read."""
        keys: list[str] = []
        for window, language, url in self.trending_urls(settings):
            key = page_key(window, language, day)
            if cache.exists(key):
                keys.append(key)
                continue
            try:
                cache.write_text(key, self.get(url))
                keys.append(key)
            except FetchError as exc:
                log.error("skipping %s: %s", url, exc)
            self.sleep(DELAY_SECONDS)
        return keys
