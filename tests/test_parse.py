# tests/test_parse.py
from pathlib import Path

from src.rank.parse import parse_trending_html

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def test_mini_monthly_parses_known_values():
    html = (FIXTURES / "trending_mini.html").read_text(encoding="utf-8")
    rows = parse_trending_html(html, "monthly")
    assert len(rows) == 1
    row = rows[0]
    assert row["repo"] == "octocat/hello-world"
    assert row["m30"] == 1000
    assert row["stars"] == 1234
    assert row["lang"] == "Python"
    assert row["desc"] == "A tiny demo & sample"
    assert row["w7"] is None


def test_mini_weekly_parses_only_matching_period():
    html = (FIXTURES / "trending_mini.html").read_text(encoding="utf-8")
    rows = parse_trending_html(html, "weekly")
    assert len(rows) == 1
    assert rows[0]["repo"] == "foo/bar"
    assert rows[0]["w7"] == 88
    assert rows[0]["stars"] == 0


def test_unknown_period_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_trending_html("<article class='Box-row'></article>", "fortnight")


def test_real_monthly_sample():
    html = (SAMPLES / "trending_monthly.html").read_text(encoding="utf-8")
    rows = parse_trending_html(html, "monthly")
    assert len(rows) == 3
    for row in rows:
        assert "/" in row["repo"]
        assert row["m30"] > 0
        assert row["stars"] > 0
    gains = [r["m30"] for r in rows]
    assert gains == sorted(gains, reverse=True)  # Trending is pre-sorted


def test_real_weekly_and_daily_samples():
    weekly = parse_trending_html(
        (SAMPLES / "trending_weekly.html").read_text(encoding="utf-8"), "weekly")
    daily = parse_trending_html(
        (SAMPLES / "trending_daily.html").read_text(encoding="utf-8"), "daily")
    assert len(weekly) == 3 and all(r["w7"] > 0 for r in weekly)
    assert len(daily) == 3 and all(r["d1"] > 0 for r in daily)
