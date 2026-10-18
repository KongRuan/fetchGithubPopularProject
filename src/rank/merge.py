"""Merge parsed Trending pages, apply validation rules, and rank."""
from __future__ import annotations

from typing import Iterable

FIELDS = ("repo", "stars", "m30", "w7", "d1", "desc", "lang", "source")

#: Estimates may exceed the current total only by star churn; anything larger
#: is dirty data (see spec amendment: predictionio vs rea).
ESTIMATE_MARGIN = 1.10

#: Source labels recorded on every record.
SOURCE_TRENDING = "trending"
SOURCE_EST_WEEK = "est-week"
SOURCE_EST_DAY = "est-day"
SOURCE_EST_FAILED = "est-failed"
SOURCE_DROPPED = "dropped"

_GAIN_FIELDS = ("m30", "w7", "d1")


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


def merge_records(pages: Iterable[list[dict]]) -> dict[str, dict]:
    """Fold many page-parse results into one record per repository.

    Gain fields keep the largest value seen (the same window reported by two
    pages should agree; taking max makes the result independent of fetch order).
    ``stars``/``desc``/``lang`` fill with the first non-empty value.
    """
    records: dict[str, dict] = {}
    for page in pages:
        for row in page:
            rec = records.setdefault(row["repo"], _blank(row["repo"]))
            for field in _GAIN_FIELDS:
                value = row.get(field)
                if value is None:
                    continue
                current = rec[field]
                rec[field] = value if current is None else max(current, value)
            if row.get("stars"):
                rec["stars"] = max(rec["stars"], int(row["stars"]))
            if not rec["desc"] and row.get("desc"):
                rec["desc"] = row["desc"]
            if not rec["lang"] and row.get("lang"):
                rec["lang"] = row["lang"]
    return records


def apply_sanity_rules(records: dict[str, dict]) -> None:
    """Discard estimates that cannot be reconciled with the total star count.

    Official Trending figures are data of record and are never discarded.
    Mutates records in place.
    """
    for rec in records.values():
        gain = rec.get("m30")
        total = rec.get("stars") or 0
        if gain is None or not total:
            continue
        is_estimate = str(rec.get("source", "")).startswith("est-")
        if is_estimate and gain > total * ESTIMATE_MARGIN:
            rec["m30"] = None
            rec["source"] = SOURCE_DROPPED


def rank(records: dict[str, dict], top_n: int) -> tuple[list[dict], list[dict]]:
    """Return ``(top, rest)`` sorted by 30-day gain descending.

    Repositories without a 30-day figure are excluded from both lists.
    Ties break on repo name so output is deterministic.
    """
    eligible = [r for r in records.values() if r.get("m30") is not None]
    eligible.sort(key=lambda r: (-r["m30"], r["repo"]))
    return eligible[:top_n], eligible[top_n:]
