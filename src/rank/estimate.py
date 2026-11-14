"""Fill missing Trending gains from GitHub's history, then OSSInsight.

GitHub Trending's published window gain is authoritative. For repositories
it doesn't cover, first sum GitHub's privacy-safe daily star counts. If that
history is unavailable or incomplete, try OSSInsight's star-history series.
Both historical sources carry an explicit ``est-*`` label so they are not
mistaken for Trending figures.

OSSInsight declares its data quality degraded, so its delta is a lower bound
and the report labels it ``估算``.

Contract: ``merge.apply_sanity_rules`` drops any ``est-*`` record whose
``m30 > stars * 1.10`` and any ``est-*`` record with no total at all. This
module therefore never sets ``m30`` on a record it marks ``est-failed`` —
the failure path leaves ``m30`` as ``None`` so the prefix test stays sound.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import requests

from .merge import SOURCE_EST_DAY, SOURCE_EST_FAILED, SOURCE_EST_GITHUB, SOURCE_EST_WEEK

log = logging.getLogger(__name__)

API = "https://api.ossinsight.io/v1/repos/{owner}/{name}/stargazers/history/"
DELAY_SECONDS = 0.25
# The public OSSInsight API has returned 500 for multiple repositories.
# One bad host must not turn a leaderboard run into a 20-minute
# retry spiral across hundreds of repositories, so a run that sees nothing
# but failures stops calling the API at all.
MAX_CONSECUTIVE_FAILURES = 5
_global_failures = 0


class FetchError(Exception):
    """Raised when a star-history request fails after all retries."""


def history_url(owner: str, name: str, period: str,
                date_from: date, date_to: date) -> str:
    return (
        f"{API.format(owner=owner, name=name)}"
        f"?per={period}&from={date_from.isoformat()}&to={date_to.isoformat()}"
    )


def history_delta(rows: list[dict]) -> int | None:
    """Stars gained across the window, or None when the series is too short.

    The delta is ``last - first`` over the window; fewer than 2 data points
    yields None, which becomes ``est-failed``. That guard exists because of
    the dirty-series failure mode (e.g. apache/predictionio), where a single
    point cannot support any delta at all.
    """
    if not rows or len(rows) < 2:
        return None
    first = Decimal(str(rows[0]["stargazers"]).replace(",", ""))
    last = Decimal(str(rows[-1]["stargazers"]).replace(",", ""))
    return int(last - first)


def _history_key(owner: str, name: str, period: str,
                 date_from: date, date_to: date, day: str) -> str:
    return f"est/{day}/{owner}@{name}_{period}_{date_from}_{date_to}.json"


def _github_key(owner: str, name: str, day: str) -> str:
    return f"github_history/{day}/{owner}@{name}.json"


def fetch_github_history(owner: str, name: str, cache, day: str, *,
                         session=None, token: str = "") -> list[dict]:
    """Fetch GitHub's weekly star history, caching it separately from OSSInsight."""
    key = _github_key(owner, name, day)
    cached = cache.read_json(key) if cache is not None else None
    if cached is not None:
        return cached
    url = f"https://api.github.com/repos/{owner}/{name}/stargazers/history"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "fetchGithubPopularProject",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    get = session.get if session is not None else requests.get
    response = get(url, headers=headers, timeout=10)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("GitHub star history is not a list")
    if cache is not None:
        cache.write_json(key, rows)
    return rows


def github_window_gain(rows: list[dict], date_from: date,
                       date_to: date) -> int | None:
    """Sum the API's daily star creations, only for a fully covered window.

    GitHub returns newest weeks first; ``week`` is the Unix timestamp of the
    Sunday starting that week. Incomplete windows fall through to OSSInsight
    rather than treating missing days as zero.
    """
    if not rows or date_from > date_to:
        return None
    daily: dict[date, int] = {}
    for row in rows:
        week = datetime.fromtimestamp(row["week"], timezone.utc).date()
        days = row["days"]
        if len(days) != 7 or any(type(value) is not int or value < 0
                                 for value in days):
            raise ValueError("invalid GitHub daily star counts")
        for index, value in enumerate(days):
            daily[week + timedelta(days=index)] = value
    gain = 0
    current = date_from
    while current <= date_to:
        if current not in daily:
            return None
        gain += daily[current]
        current += timedelta(days=1)
    return gain


def fill_github_estimates(records: dict, *, enable: bool = True,
                          date_from: date, date_to: date,
                          history: Callable, cache, day: str) -> None:
    """Prefer GitHub's daily history for missing gains; leave gaps for fallback."""
    if not enable:
        return
    failures = 0
    rate_limited = False
    for rec in records.values():
        if rec.get("m30") is not None:
            continue
        pair = _owner_name(rec["repo"])
        if pair is None:
            continue
        owner, name = pair
        if rate_limited or failures >= MAX_CONSECUTIVE_FAILURES:
            rows = (cache.read_json(_github_key(owner, name, day))
                    if cache is not None else None)
            if rows is None:
                continue
        else:
            try:
                rows = history(owner, name, cache, day)
                failures = 0
            except requests.HTTPError as exc:
                response = exc.response
                if response is not None and response.status_code in (403, 429):
                    rate_limited = True
                    log.warning("GitHub star history unavailable (HTTP %d); "
                                "falling back to OSSInsight", response.status_code)
                else:
                    failures += 1
                    log.warning("GitHub history failed for %s: %s", rec["repo"], exc)
                continue
            except (requests.RequestException, OSError, ValueError) as exc:
                failures += 1
                log.warning("GitHub history failed for %s: %s", rec["repo"], exc)
                continue
        try:
            gain = github_window_gain(rows, date_from, date_to)
        except (TypeError, ValueError, KeyError, OverflowError, OSError) as exc:
            log.warning("invalid GitHub history for %s: %s", rec["repo"], exc)
            continue
        if gain is not None:
            rec["m30"] = gain
            rec["source"] = SOURCE_EST_GITHUB


def fetch_history(
    owner: str,
    name: str,
    period: str,
    date_from: date,
    date_to: date,
    cache,
    day: str,
    *,
    session=None,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 3,
) -> list[dict]:
    """Return star-history rows, cached per repo/period/window/day.

    Retries transient transport and HTTP errors with exponential backoff on
    DELAY_SECONDS; a definitive failure propagates so the caller can mark the
    record ``est-failed``. Cache hits return before any network call or sleep.

    ``session`` should be a shared ``requests.Session`` in production: without
    one, every request pays a fresh TLS handshake, which dominates a ~370-repo
    sweep. Tests pass none and stub ``requests.get`` directly.

    The cache key embeds day + repo + period + window, so same-day re-runs are
    free and a new day refetches.
    """
    key = _history_key(owner, name, period, date_from, date_to, day)
    cached = cache.read_json(key) if cache is not None else None
    if cached is not None:
        return cached

    url = history_url(owner, name, period, date_from, date_to)
    get = session.get if session is not None else requests.get
    delay = DELAY_SECONDS
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = get(
                url,
                headers={"Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            rows = (response.json().get("data") or {}).get("rows") or []
            break
        except (requests.RequestException, OSError, ValueError) as exc:
            # Narrow on purpose: transport/HTTP trouble and a malformed JSON
            # body are retryable. Anything else is a programming error and
            # must surface immediately rather than be retried and rebranded
            # as a network failure.
            last = exc
            log.warning("history attempt %d/%d failed for %s/%s: %s: %s",
                        attempt, retries, owner, name, type(exc).__name__, exc)
            if attempt < retries:
                sleep(delay)
                delay *= 2
    else:
        raise FetchError(
            f"GET {url} failed after {retries} attempts: "
            f"{type(last).__name__}: {last}"
        ) from last

    if cache is not None:
        cache.write_json(key, rows)
    return rows


def _owner_name(repo: str) -> tuple[str, str] | None:
    """Split ``owner/name``. Names may themselves contain slashes; owner may not.

    A repo id with no slash cannot be turned into an OSSInsight URL. The caller
    records ``est-failed`` rather than guessing.
    """
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return owner, name


def fill_estimates(records: dict, *, period_days: int, enable: bool,
                   date_from: date, date_to: date,
                   history: Callable, cache, day: str) -> None:
    """Fill ``m30`` for records that have no window gain.

    A 30-day window uses ``per=week`` (``period_days > 7``): weekly points are
    enough to span the window and cost OSSInsight far less than a daily series.

    Mutates records in place. Failures are recorded as ``est-failed`` and
    never abort the run — one bad repository must not kill a ~180-repo batch.

    An empty series is treated exactly like a fetch failure. That is
    deliberate: a repo OSSInsight has not indexed yet cannot produce a delta
    either, and the conservative outcome (no rank, rather than a fabricated
    +0) can only ever exclude a repository that gained nothing. It cannot
    promote a wrong one.
    """
    if not enable:
        return
    period = "week" if period_days > 7 else "day"
    global _global_failures
    _global_failures = 0
    for rec in records.values():
        if rec.get("m30") is not None:
            continue
        pair = _owner_name(rec["repo"])
        if pair is None:
            rec["source"] = SOURCE_EST_FAILED
            continue
        owner, name = pair
        if _global_failures >= MAX_CONSECUTIVE_FAILURES:
            # A failed host must not hide earlier successful, cached estimates.
            rows = (
                cache.read_json(_history_key(owner, name, period,
                                             date_from, date_to, day))
                if cache is not None else None
            )
            if rows is None:
                rec["source"] = SOURCE_EST_FAILED
                continue
        else:
            try:
                rows = history(owner, name, period, date_from, date_to, cache, day)
                _global_failures = 0
            except Exception as exc:  # noqa: BLE001 - one repo must not kill the run
                _global_failures += 1
                log.warning("estimate failed for %s: %s", rec["repo"], exc)
                if _global_failures == MAX_CONSECUTIVE_FAILURES:
                    log.warning(
                        "OSSInsight unavailable; using cached history only "
                        "for remaining repositories"
                    )
                rec["source"] = SOURCE_EST_FAILED
                continue
        delta = history_delta(rows)
        if delta is None:
            rec["source"] = SOURCE_EST_FAILED
            continue
        rec["m30"] = delta
        rec["source"] = SOURCE_EST_WEEK


def revalidate(records: list[dict], *, date_from: date, date_to: date,
               history: Callable, cache, day: str) -> None:
    """Re-fetch estimates that made the report using the denser daily series.

    Only OSSInsight weekly estimates on the already-ranked top list are
    rechecked; GitHub history already provides daily counts and never needs
    a request to the backup service.

    ``records`` is the ranked top list; entries are updated in place.
    """
    global _global_failures
    _global_failures = 0
    for rec in records:
        if rec.get("source") != SOURCE_EST_WEEK:
            continue
        if _global_failures >= MAX_CONSECUTIVE_FAILURES:
            return
        pair = _owner_name(rec["repo"])
        if pair is None:
            continue
        owner, name = pair
        try:
            rows = history(owner, name, "day", date_from, date_to, cache, day)
            _global_failures = 0
        except Exception as exc:  # noqa: BLE001 - one repo must not kill the run
            _global_failures += 1
            log.warning("revalidate failed for %s: %s", rec["repo"], exc)
            continue
        delta = history_delta(rows)
        if delta is not None:
            rec["m30"] = delta
            rec["source"] = SOURCE_EST_DAY
