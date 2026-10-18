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
