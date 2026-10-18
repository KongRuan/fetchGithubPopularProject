"""Merge parsed Trending pages, apply validation rules, and rank.

A record, keyed by ``"owner/name"``, has this shape from here on::

    {
        "repo": "owner/name",
        "stars": 12000,          # lifetime total; 0 when the page had none
        "m30": 800,              # stars gained in the report window, or None
        "w7": 200,               # official 7-day gain, reference column only
        "d1": None,              # official 1-day gain; not used by the report
        "desc": "...",
        "lang": "Python",
        "source": "trending",    # see SOURCE_* below
    }

``m30`` is the sort key even when ``period_days`` is 1 or 7: the field name is
historical (the default window is 30 days) and every window writes into it.
``source`` says where ``m30`` came from. Only ``est-*`` values are subject to
``apply_sanity_rules``; ``trending`` is never discarded.
"""
from __future__ import annotations

from typing import Iterable

#: Estimates may exceed the current total only by star churn; anything larger
#: is dirty data (see spec amendment: predictionio vs rea).
ESTIMATE_MARGIN = 1.10

#: Source labels recorded on every record.
#: trending    — GitHub Trending's own "stars this ..." figure (authoritative)
#: est-week    — OSSInsight weekly series, the cheap first pass
#: est-day     — same API, daily series, written only by estimate.revalidate
#: est-failed  — no usable series; m30 stays None and the repo cannot rank
#: dropped     — an est-* figure failed the sanity check and m30 was cleared
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
    ``stars`` likewise keeps the largest non-zero value, while ``desc`` and
    ``lang`` fill with the first non-empty value.
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
    An estimate with no total to check against is dropped too: the margin rule
    is the only thing standing between an unchecked number and the report, so
    silently skipping it would make the rule a no-op for that repository.
    Mutates records in place.
    """
    for rec in records.values():
        gain = rec.get("m30")
        if gain is None:
            continue
        if not str(rec.get("source", "")).startswith("est-"):
            continue
        total = rec.get("stars") or 0
        if not total or gain > total * ESTIMATE_MARGIN:
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
