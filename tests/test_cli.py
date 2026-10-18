# tests/test_cli.py
import json
import logging
from datetime import date
from pathlib import Path

from src.rank import cli

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def _write_config(root: Path) -> Path:
    cfg = root / "config.yaml"
    cfg.write_text(
        "period_days: 30\n"
        "top_n: 2\n"
        "languages: [python]\n"
        'github_token: ""\n'
        "enable_ossinsight_estimate: false\n"
        "output_dir: output\n",
        encoding="utf-8",
    )
    return cfg


def _fill_cache(root: Path, day: str) -> None:
    """Populate every cache key the pipeline will look for, offline.

    The global (``_all``) pages reuse the recorded sample HTML; the per-language
    pages are empty (a page with no articles parses to zero records).
    """
    cache_dir = root / "data" / "cache" / "pages" / day
    cache_dir.mkdir(parents=True)
    for window in ("daily", "weekly", "monthly"):
        (cache_dir / f"trending_{window}_all.html").write_text(
            (SAMPLES / f"trending_{window}.html").read_text(encoding="utf-8"),
            encoding="utf-8")
        (cache_dir / f"trending_{window}_python.html").write_text(
            "", encoding="utf-8")


def test_render_step_from_cached_pages(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    cache_dir = tmp_path / "data" / "cache" / "pages" / "2026-10-18"
    cache_dir.mkdir(parents=True)
    # two different pages holding the same repos, disjoint gain fields
    (cache_dir / "trending_monthly_all.html").write_text(
        (SAMPLES / "trending_monthly.html").read_text(encoding="utf-8"),
        encoding="utf-8")
    (cache_dir / "trending_weekly_all.html").write_text(
        (SAMPLES / "trending_weekly.html").read_text(encoding="utf-8"),
        encoding="utf-8")

    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg), "merge"]) == 0
    assert cli.main(["--config", str(cfg), "render"]) == 0

    out = tmp_path / "output" / "rank_2026-10-18.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "# GitHub 新增 Star 榜单（近 30 天）" in text
    assert "https://github.com/" in text

    archived = json.loads(
        (tmp_path / "data" / "results" / "rank_2026-10-18.json")
        .read_text(encoding="utf-8"))
    assert archived["run_date"] == "2026-10-18"
    assert len(archived["top"]) == 2


def test_missing_cache_reports_error(tmp_path):
    cfg = _write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "render"]) == 1


def test_full_pipeline_runs_offline_end_to_end(tmp_path, monkeypatch):
    """No subcommand runs fetch -> merge -> render; pre-populated cache keys
    mean fetch makes zero network calls."""
    cfg = _write_config(tmp_path)
    _fill_cache(tmp_path, "2026-10-18")

    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg)]) == 0
    assert (tmp_path / "output" / "rank_2026-10-18.md").is_file()


def test_render_uses_the_merge_date_not_today(tmp_path, monkeypatch):
    """A render days after the merge must be labelled with the data's date,
    not the day the report happened to be written."""
    cfg = _write_config(tmp_path)
    cache_dir = tmp_path / "data" / "cache" / "pages" / "2026-10-18"
    cache_dir.mkdir(parents=True)
    (cache_dir / "trending_monthly_all.html").write_text(
        (SAMPLES / "trending_monthly.html").read_text(encoding="utf-8"),
        encoding="utf-8")

    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg), "merge"]) == 0

    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 20))
    assert cli.main(["--config", str(cfg), "render"]) == 0

    out = tmp_path / "output" / "rank_2026-10-18.md"
    assert out.is_file()
    assert not (tmp_path / "output" / "rank_2026-10-20.md").exists()
    text = out.read_text(encoding="utf-8")
    assert "报告日期：2026-10-18" in text
    assert "2026-09-18 → 2026-10-18" in text


def test_config_error_returns_exit_code_2(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("period_days: 45\n", encoding="utf-8")
    assert cli.main(["--config", str(cfg), "render"]) == 2


def test_fetch_step_warns_on_partial_pages(tmp_path, monkeypatch, caplog):
    """One bad language slug must not abort the run, but the page loss must
    never be silent either."""

    class StubFetcher:
        def fetch_all(self, settings, cache, day):
            return [f"pages/{day}/trending_{window}_{slug}.html"
                    for window in ("daily", "weekly")
                    for slug in ("all", "python")]

    cfg = _write_config(tmp_path)  # 3 windows x (all + python) expected = 6
    monkeypatch.setattr(cli, "TrendingFetcher", StubFetcher)
    with caplog.at_level(logging.WARNING):
        assert cli.main(["--config", str(cfg), "fetch"]) == 0
    assert "only 4/6" in caplog.text
