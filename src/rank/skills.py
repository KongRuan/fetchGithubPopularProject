"""AI agent-skill leaderboard.

Ranking of GitHub repositories that are (or curate) AI coding-agent skills.
The candidate pool comes from the GitHub Search API and every row keeps its
``created_at``, which decides how its window gain can be measured:

* created inside the window — the repository held no stars when the window
  opened, so its current total IS its gain (``search-new``, a real number);
* older than the window — measured against this tool's own daily star ledger
  (``snapshot``). Until the ledger has a reading at or before the window start
  the gain is unknown and the repository cannot rank; nothing is invented.

The local ledger exists because OSSInsight's public star-history API has been
failing (HTTP 500) since late 2026. Pools, the ledger and the report are all
cached per day, like every other network step in the pipeline.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import date, timedelta

import requests

from .cache import Cache
from .config import Settings
from .merge import (
    SOURCE_EST_FAILED,
    SOURCE_SEARCH_NEW,
    SOURCE_SNAPSHOT,
    SOURCE_TRENDING,
)

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.github.com/search/repositories"
USER_AGENT = "fetchGithubPopularProject"
PER_PAGE = 50
# Search API rate limit is 10/min anonymous, 30/min authenticated. A generous
# inter-query gap keeps a fresh day's search from tripping the limiter.
SEARCH_DELAY = 6.0

# Targeted queries: topic tags for the major skill ecosystems plus phrase
# searches for repositories that describe skills in prose rather than by topic.
SKILL_QUERIES = (
    "topic:agent-skills",
    "topic:agent-skill",
    "topic:claude-skills",
    "topic:codex-skills",
    "topic:cursor-skills",
    "topic:opencode-skills",
    '"agent skills"',
    '"claude skills"',
    "awesome agent skills",
)

#: Ledger directory: one JSON file per day mapping ``repo`` to its star total.
STARS_ROOT = "stars"


def _blank(repo: str) -> dict:
    return {
        "repo": repo,
        "stars": 0,
        "m30": None,
        "w7": None,
        "d1": None,
        "desc": "",
        "lang": "",
        "source": SOURCE_TRENDING,
    }


def _previous_day(day: str) -> str:
    """Calendar day before ``day`` (YYYY-MM-DD), for the pool fallback."""
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def _pool_key(day: str, period_days: int) -> str:
    # v2 carries ``created_at``; a v1 file cannot be upgraded in place.
    return f"skills/{day}/candidates_v2_{period_days}d.json"


def search_skill_repos(
    session,
    date_from: date,
    token: str = "",
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Return deduped skill repositories: new ones plus the biggest existing.

    Each query runs twice — scoped to ``created:>{date_from}`` to catch this
    window's releases, and unscoped to catch established skill collections.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    found: dict[str, dict] = {}
    tails = (f" created:>{date_from.isoformat()}", "")
    for tail in tails:
        for query in SKILL_QUERIES:
            try:
                response = session.get(
                    SEARCH_URL,
                    headers=headers,
                    params={"q": query + tail, "sort": "stars", "order": "desc",
                            "per_page": PER_PAGE},
                    timeout=30,
                )
                if response.status_code == 403:
                    log.warning("search rate-limited for %r, skipping", query)
                    sleep(SEARCH_DELAY)
                    continue
                response.raise_for_status()
                for item in response.json().get("items", []):
                    found.setdefault(item["full_name"], {
                        "repo": item["full_name"],
                        "stars": int(item.get("stargazers_count") or 0),
                        "desc": item.get("description") or "",
                        "lang": item.get("language") or "",
                        "created_at": (item.get("created_at") or "")[:10],
                    })
            except (requests.RequestException, OSError, ValueError) as exc:
                log.warning("search failed for %r: %s", query, exc)
            sleep(SEARCH_DELAY)
    return list(found.values())


def write_ledger(cache: Cache, day: str, totals: dict[str, int]) -> None:
    """Store one day of star totals; a re-run keeps the earlier reading."""
    cache.write_json(f"{STARS_ROOT}/{day}.json", totals)


def read_ledger(cache: Cache, day: str) -> dict:
    data = cache.read_json(f"{STARS_ROOT}/{day}.json")
    return data if isinstance(data, dict) else {}


def ledger_baseline_day(cache: Cache, window_start: date) -> str | None:
    """Newest ledger day on or before ``window_start``, or None.

    A reading inside the window would shorten it and understate the gain, so
    those days are deliberately not used.
    """
    root = cache.root / STARS_ROOT
    if not root.is_dir():
        return None
    days = sorted((p.stem for p in root.glob("*.json")), reverse=True)
    return next((d for d in days if d <= window_start.isoformat()), None)


def build_skill_records(
    settings: Settings,
    cache: Cache,
    day: str,
    today: date,
    *,
    session=None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, dict]:
    """Return skill records with ``m30`` filled where it is measurable."""
    date_from = today - timedelta(days=settings.period_days)
    context = nullcontext(session) if session is not None else requests.Session()
    with context as http:
        pool = cache.read_json(_pool_key(day, settings.period_days))
        if not isinstance(pool, list) or not pool:
            # After midnight the previous pool is still a fine candidate list;
            # ``created_at`` below decides how each row's gain is measured.
            pool = cache.read_json(
                _pool_key(_previous_day(day), settings.period_days))
            if not isinstance(pool, list) or not pool:
                pool = search_skill_repos(
                    http, date_from=date_from, token=settings.github_token,
                    sleep=sleep)
                cache.write_json(
                    _pool_key(day, settings.period_days), pool)

        records: dict[str, dict] = {}
        for row in pool:
            rec = _blank(row["repo"])
            rec.update({
                "stars": row.get("stars") or 0,
                "desc": row.get("desc") or "",
                "lang": row.get("lang") or "",
                "created_at": row.get("created_at") or "",
            })
            records[rec["repo"]] = rec

        _measure(records, cache, day, date_from)
        _apply_same_day_trending(records, cache, day)
        return records


def _measure(records: dict[str, dict], cache: Cache, day: str,
             date_from: date) -> None:
    """Fill ``m30`` for every candidate whose gain is knowable.

    The day's ledger is written first so the ledger grows one file per run,
    whichever candidates today's search happened to return.
    """
    write_ledger(cache, day, {r["repo"]: r["stars"] for r in records.values()})
    baseline_day = ledger_baseline_day(cache, date_from)
    baseline = read_ledger(cache, baseline_day) if baseline_day else {}
    for rec in records.values():
        earlier = baseline.get(rec["repo"])
        if _born_in_window(rec, date_from):
            rec["m30"], rec["source"] = rec["stars"], SOURCE_SEARCH_NEW
        elif isinstance(earlier, int) and earlier < rec["stars"]:
            rec["m30"], rec["source"] = rec["stars"] - earlier, SOURCE_SNAPSHOT
        else:
            rec["m30"], rec["source"] = None, SOURCE_EST_FAILED


def _apply_same_day_trending(records: dict[str, dict], cache: Cache,
                             day: str) -> None:
    """Let the same-day Trending figure win; an older merge is a different
    window and must not be mixed in."""
    merged = cache.read_json("merged.json")
    if not (isinstance(merged, dict) and merged.get("merged_on") == day
            and isinstance(merged.get("records"), dict)):
        return
    for repo, mrec in merged["records"].items():
        if repo in records and mrec.get("m30") is not None:
            records[repo]["m30"] = mrec["m30"]
            records[repo]["source"] = mrec.get("source", SOURCE_TRENDING)


def _born_in_window(rec: dict, date_from: date) -> bool:
    """True when the repository did not exist when the window opened."""
    created = rec.get("created_at")
    if not created:
        return False
    try:
        return date.fromisoformat(created) >= date_from
    except ValueError:
        return False
