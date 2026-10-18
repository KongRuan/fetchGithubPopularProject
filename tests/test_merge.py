# tests/test_merge.py
from src.rank.merge import (
    ESTIMATE_MARGIN,
    apply_sanity_rules,
    merge_records,
    rank,
)


def _row(repo, stars=0, m30=None, w7=None, d1=None, desc="", lang=""):
    return {"repo": repo, "stars": stars, "m30": m30, "w7": w7,
            "d1": d1, "desc": desc, "lang": lang}


def test_merge_dedupes_and_takes_max_gain():
    pages = [
        [_row("a/b", stars=100, m30=50, desc="first", lang="Go")],
        [_row("a/b", stars=100, m30=60, desc="", lang="")],
    ]
    recs = merge_records(pages)
    assert recs["a/b"]["m30"] == 60          # same window, keep the larger figure
    assert recs["a/b"]["desc"] == "first"    # first non-empty wins
    assert recs["a/b"]["lang"] == "Go"


def test_merge_keeps_fields_from_different_periods():
    pages = [
        [_row("a/b", stars=10, m30=100)],
        [_row("a/b", stars=999, w7=7)],
    ]
    recs = merge_records(pages)
    assert recs["a/b"]["m30"] == 100
    assert recs["a/b"]["w7"] == 7
    assert recs["a/b"]["stars"] == 999


def test_sanity_drops_only_outlier_estimates():
    recs = {
        # estimate 7% over total -> tolerated churn, kept
        "keep": _row("keep", stars=1000, m30=1070),
        # estimate 20% over total -> dirty data, dropped
        "drop": _row("drop", stars=1000, m30=1200),
        # official Trending figure over total -> never dropped
        "official": _row("official", stars=1000, m30=5000),
    }
    recs["keep"]["source"] = "est-week"
    recs["drop"]["source"] = "est-week"
    recs["official"]["source"] = "trending"
    apply_sanity_rules(recs)
    assert recs["keep"]["m30"] == 1070
    assert recs["drop"]["m30"] is None
    assert recs["drop"]["source"] == "dropped"
    assert recs["official"]["m30"] == 5000


def test_rank_sorts_descending_and_truncates():
    recs = {
        "low": _row("low", m30=5),
        "high": _row("high", m30=500),
        "mid": _row("mid", m30=50),
        "none": _row("none", m30=None),
    }
    top, rest = rank(recs, top_n=2)
    assert [r["repo"] for r in top] == ["high", "mid"]
    assert [r["repo"] for r in rest] == ["low"]


def test_rank_breaks_ties_by_repo_name():
    recs = {"z/b": _row("z/b", m30=10), "a/b": _row("a/b", m30=10)}
    top, _ = rank(recs, top_n=2)
    assert [r["repo"] for r in top] == ["a/b", "z/b"]


def test_margin_constant_is_documented():
    assert ESTIMATE_MARGIN == 1.10


def test_margin_boundary_is_exclusive():
    """1100/1000 must be kept and 1101/1000 dropped: `>`, not `>=`."""
    recs = {
        "exact": _row("exact", stars=1000, m30=1100),
        "over": _row("over", stars=1000, m30=1101),
        "under": _row("under", stars=1000, m30=1099),
    }
    for rec in recs.values():
        rec["source"] = "est-week"
    apply_sanity_rules(recs)
    assert recs["exact"]["m30"] == 1100
    assert recs["under"]["m30"] == 1099
    assert recs["over"]["m30"] is None
    assert recs["over"]["source"] == "dropped"


def test_estimate_without_a_total_is_unverifiable_and_dropped():
    """With stars==0 the margin rule has nothing to check against, so the
    estimate must not reach the report unvalidated."""
    recs = {"no_total": _row("no_total", stars=0, m30=999999)}
    recs["no_total"]["source"] = "est-week"
    apply_sanity_rules(recs)
    assert recs["no_total"]["m30"] is None
    assert recs["no_total"]["source"] == "dropped"


def test_official_figure_without_a_total_is_still_kept():
    """Official Trending figures are data of record, totals or not."""
    recs = {"official": _row("official", stars=0, m30=500)}
    recs["official"]["source"] = "trending"
    apply_sanity_rules(recs)
    assert recs["official"]["m30"] == 500
