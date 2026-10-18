"""Estimate missing window gains from OSSInsight star history.

GitHub Trending's official ``stars this month`` figure is authoritative, but
Trending publishes it only for repositories currently on the page. Records
without a 30-day figure get one estimated from OSSInsight's star-history API
and are flagged as estimates (``est-*``), never merged silently with the
official numbers.

OSSInsight declares its data quality degraded, so the values here are a
LOWER BOUND on the true gain — hence every value this module produces carries
an estimate source label that the report renders as ``估算``.

Contract: ``merge.apply_sanity_rules`` drops any ``est-*`` record whose
``m30 > stars * 1.10`` and any ``est-*`` record with no total at all. This
module therefore never sets ``m30`` on a record it marks ``est-failed`` —
the failure path leaves ``m30`` as ``None`` so the prefix test stays sound.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from decimal import Decimal
from typing import Callable

import requests

from .merge import SOURCE_EST_DAY, SOURCE_EST_FAILED, SOURCE_EST_WEEK

log = logging.getLogger(__name__)

API = "https://api.ossinsight.io/v1/repos/{owner}/{name}/stargazers/history/"
DELAY_SECONDS = 0.25


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
    key = f"est/{day}/{owner}@{name}_{period}_{date_from}_{date_to}.json"
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
    for rec in records.values():
        if rec.get("m30") is not None:
            continue
        pair = _owner_name(rec["repo"])
        if pair is None:
            rec["source"] = SOURCE_EST_FAILED
            continue
        owner, name = pair
        try:
            rows = history(owner, name, period, date_from, date_to, cache, day)
        except Exception as exc:  # noqa: BLE001 - one repo must not kill the run
            log.warning("estimate failed for %s: %s", rec["repo"], exc)
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

    Only records whose ``source`` starts with ``est-`` are touched, and
    ``records`` is the already-ranked top list — so this never wastes calls on
    repositories that missed the report. The daily series is the cross-check
    for estimates near the report cutoff.

    ``records`` is the ranked top list; entries are updated in place.
    """
    for rec in records:
        if not str(rec.get("source", "")).startswith("est-"):
            continue
        pair = _owner_name(rec["repo"])
        if pair is None:
            continue
        owner, name = pair
        try:
            rows = history(owner, name, "day", date_from, date_to, cache, day)
        except Exception as exc:  # noqa: BLE001 - one repo must not kill the run
            log.warning("revalidate failed for %s: %s", rec["repo"], exc)
            continue
        delta = history_delta(rows)
        if delta is not None:
            rec["m30"] = delta
            rec["source"] = SOURCE_EST_DAY
