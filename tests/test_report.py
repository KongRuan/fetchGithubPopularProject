# tests/test_report.py
from datetime import date

from src.rank.report import render_markdown


def _rec(repo, m30, stars, w7=None, desc="", lang="", source="trending"):
    return {"repo": repo, "stars": stars, "m30": m30, "w7": w7,
            "d1": None, "desc": desc, "lang": lang, "source": source}


def test_report_contains_header_table_and_citations():
    top = [
        _rec("octo/one", 5000, 9000, w7=700, desc="第一个", lang="Python"),
        _rec("octo/two", 4000, 12000, w7=None, desc="", lang="Go",
             source="est-week"),
    ]
    rest = [_rec("octo/three", 3000, 4000)]
    md = render_markdown(top, rest, period_days=30, top_n=2,
                         scanned=123, run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))

    assert "# GitHub 新增 Star 榜单（近 30 天）" in md
    assert (
        "| 1 | **[octo/one](https://github.com/octo/one)** "
        "| +5,000 | 9,000 | +700 | Python | 第一个 | 官方 |" in md
    )
    assert (
        "| 2 | **[octo/two](https://github.com/octo/two)** "
        "| +4,000 | 12,000 | — | Go |  | 估算 |" in md
    )
    assert "估算" in md                       # source flag rendered
    assert "共扫描 123 个仓库" in md
    assert "2026-09-18 → 2026-10-18" in md
    assert "octo/three" in md                 # runners-up section


def test_report_escapes_pipe_in_description():
    top = [_rec("a/b", 10, 20, desc="a | b")]
    md = render_markdown(top, [], period_days=30, top_n=1, scanned=1,
                         run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    assert "a \\| b" in md


def test_report_shows_dash_when_weekly_missing():
    md = render_markdown([_rec("a/b", 10, 20, w7=None)], [],
                         period_days=30, top_n=1, scanned=1,
                         run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    assert "| — |" in md


def test_methodology_states_the_real_110_percent_rule():
    """The rule is `gain > total * 1.10`. Saying "10%" told readers the
    pipeline was ~11x more aggressive than it is."""
    md = render_markdown([_rec("a/b", 10, 20)], [], period_days=30, top_n=1,
                         scanned=1, run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    assert "超过总星标的 110%" in md
    assert "官方值不受此规则影响" in md
    assert "排序键为 30 天新增 star 数" in md


def test_runners_up_is_capped_at_five():
    rest = [_rec(f"o/r{i}", 100 - i, 1000) for i in range(9)]
    md = render_markdown([], rest, period_days=30, top_n=0, scanned=9,
                         run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    assert md.count("- `") == 5
    assert "`o/r4`" in md
    assert "`o/r5`" not in md


def test_source_label_covers_every_known_source_and_falls_back():
    for source, label in [("trending", "官方"), ("est-week", "估算"),
                          ("est-day", "估算"), ("est-failed", "估算失败"),
                          ("dropped", "已剔除")]:
        md = render_markdown([_rec("a/b", 10, 20, source=source)], [],
                             period_days=30, top_n=1, scanned=1,
                             run_date=date(2026, 10, 18),
                             window=("2026-09-18", "2026-10-18"))
        assert f"| {label} |" in md
    md = render_markdown([_rec("a/b", 10, 20, source="future-kind")], [],
                         period_days=30, top_n=1, scanned=1,
                         run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    assert "| future-kind |" in md


def test_description_carriage_return_does_not_break_the_row():
    md = render_markdown([_rec("a/b", 10, 20, desc="x\ry")], [],
                         period_days=30, top_n=1, scanned=1,
                         run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    row = [ln for ln in md.splitlines() if ln.startswith("| 1 |")][0]
    assert "\r" not in row
    assert "x y" in row


def test_negative_gain_renders_with_a_single_sign():
    """`f"+{value}"` produced "+-5,000" for a repo that lost stars."""
    md = render_markdown([_rec("a/b", -5000, 20)], [], period_days=30, top_n=1,
                         scanned=1, run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    assert "| -5,000 |" in md
    assert "+-" not in md


def test_zero_gain_is_distinct_from_missing_gain():
    md = render_markdown([_rec("a/b", 0, 20, w7=0)], [],
                         period_days=30, top_n=1, scanned=1,
                         run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))
    assert "| +0 |" in md
    assert "+0" in md


def test_keyword_only_arguments_are_enforced():
    import pytest

    with pytest.raises(TypeError):
        render_markdown([], [], 30, 20, 1, date(2026, 10, 18),
                        ("2026-09-18", "2026-10-18"))
