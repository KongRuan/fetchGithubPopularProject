# tests/test_cli.py
import json
import logging
from datetime import date
from pathlib import Path

import requests

from src.rank import cli
from src.rank.cache import Cache

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def _write_config(root: Path) -> Path:
    cfg = root / "config.yaml"
    cfg.write_text(
        "period_days: 30\n"
        "top_n: 2\n"
        "languages: [python]\n"
        'github_token: ""\n'
        "enable_ossinsight_estimate: false\n"
        "enable_github_history: false\n"
        "enable_chinese_summary: false\n"
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


def test_merge_prefers_github_history_then_falls_back_to_ossinsight(tmp_path, monkeypatch):
    from src.rank import estimate as est
    from src.rank.config import Settings

    def row(repo, gain=None):
        return {"repo": repo, "stars": 100, "m30": gain, "w7": None,
                "d1": None, "desc": "", "lang": ""}

    monkeypatch.setattr(cli, "_load_pages", lambda *args: [[
        row("official/repo", 8), row("primary/repo"), row("fallback/repo"),
    ]])
    github_calls = []
    oss_calls = []

    def github(owner, name, cache, day, **kwargs):
        github_calls.append(owner)
        if owner == "fallback":
            return []
        return [
            {"week": 1792281600, "days": [4, 0, 0, 0, 0, 0, 0], "total": 4},
            {"week": 1791676800, "days": [0, 0, 0, 0, 0, 0, 3], "total": 3},
        ]

    def oss(owner, name, period, date_from, date_to, cache, day, **kwargs):
        oss_calls.append(owner)
        return [{"stargazers": "10"}, {"stargazers": "20"}]

    monkeypatch.setattr(est, "fetch_github_history", github, raising=False)
    monkeypatch.setattr(est, "fetch_history", oss)
    settings = Settings(root=tmp_path, period_days=1, top_n=3)
    cache = Cache(tmp_path)
    assert cli.step_merge(settings, cache, "2026-10-18", date(2026, 10, 18)) == 0
    records = cache.read_json("merged.json")["records"]
    assert records["primary/repo"]["m30"] == 7
    assert records["primary/repo"]["source"] == "est-github"
    assert records["fallback/repo"]["m30"] == 10
    assert records["official/repo"]["m30"] == 8
    assert github_calls == ["primary", "fallback"]
    assert set(oss_calls) == {"fallback"}


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
    """No subcommand runs fetch -> merge -> render without external services."""
    cfg = _write_config(tmp_path)
    monkeypatch.setattr("src.rank.translate.requests.Session.post",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("offline test called translation service")))
    _fill_cache(tmp_path, "2026-10-18")

    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg)]) == 0
    assert (tmp_path / "output" / "rank_2026-10-18.md").is_file()


def test_growth_render_writes_separate_report(tmp_path, monkeypatch):
    """`render --by-growth` ranks by relative gain and writes its own file."""
    cfg = _write_config(tmp_path)
    cache_dir = tmp_path / "data" / "cache" / "pages" / "2026-10-18"
    cache_dir.mkdir(parents=True)
    (cache_dir / "trending_monthly_all.html").write_text(
        (SAMPLES / "trending_monthly.html").read_text(encoding="utf-8"),
        encoding="utf-8")

    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg), "merge"]) == 0
    assert cli.main(["--config", str(cfg), "render", "--by-growth"]) == 0

    out = tmp_path / "output" / "rank_fastest_2026-10-18.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "提升最快" in text
    assert "提升幅度" in text
    assert not (tmp_path / "output" / "rank_2026-10-18.md").exists()


def test_render_chinese_summary_uses_the_model(tmp_path, monkeypatch):
    """`render` asks the local model for a Chinese summary per ranked repo
    and caches it, so a second render makes no further calls."""
    monkeypatch.delenv("AZURE_TRANSLATOR_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    cfg = _write_config(tmp_path)
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "enable_chinese_summary: false", "enable_chinese_summary: true"),
        encoding="utf-8")
    cache_dir = tmp_path / "data" / "cache" / "pages" / "2026-10-18"
    cache_dir.mkdir(parents=True)
    (cache_dir / "trending_monthly_all.html").write_text(
        (SAMPLES / "trending_monthly.html").read_text(encoding="utf-8"),
        encoding="utf-8")

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json["input"])

        class R:
            status_code = 200

            @staticmethod
            def raise_for_status():
                pass

            @staticmethod
            def json():
                return {"output": [{"content": [{"type": "output_text",
                                                 "text": "中文简介"}]}]}

        return R()

    monkeypatch.setattr("src.rank.translate.requests.Session.get",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            requests.ReadTimeout("MyMemory unavailable")))
    monkeypatch.setattr("src.rank.translate.requests.Session.post",
                        lambda self, url, **kw: fake_post(url, **kw))
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg), "merge"]) == 0
    assert cli.main(["--config", str(cfg), "render"]) == 0
    assert calls, "the model must be called for the ranked repos"
    text = (tmp_path / "output" / "rank_2026-10-18.md").read_text(encoding="utf-8")
    assert "中文简介" in text


def test_skills_step_writes_skill_report(tmp_path, monkeypatch):
    """`skills` builds a leaderboard from a cached candidate pool, offline."""
    cfg = _write_config(tmp_path)
    cache = Cache(tmp_path / "data" / "cache")
    cache.write_json("skills/2026-10-18/candidates_v2_30d.json", [
        {"repo": "anthropics/skills", "stars": 185000,
         "desc": "Public repository for Agent Skills", "lang": "",
         "created_at": "2025-09-22"},
        {"repo": "addyosmani/agent-skills", "stars": 110000,
         "desc": "Production-grade engineering skills", "lang": "JavaScript",
         "created_at": "2026-02-15"},
        # not in the merge: a repo created inside the window, stars == gain
        {"repo": "newbie/skill", "stars": 2500,
         "desc": "A brand new agent skill", "lang": "Python",
         "created_at": "2026-10-10"},
    ])
    cache.write_json("merged.json", {
        "merged_on": "2026-10-18",
        "records": {
            "anthropics/skills": {
                "repo": "anthropics/skills", "stars": 185000, "m30": 5000,
                "w7": 1000, "d1": None, "desc": "Public repository for Agent Skills",
                "lang": "", "source": "trending",
            },
            "addyosmani/agent-skills": {
                "repo": "addyosmani/agent-skills", "stars": 110000, "m30": 3000,
                "w7": None, "d1": None, "desc": "Production-grade engineering skills",
                "lang": "JavaScript", "source": "trending",
            },
        },
    })
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg), "skills"]) == 0

    out = tmp_path / "output" / "rank_skills_2026-10-18.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "GitHub AI Skill 榜单" in text
    assert "anthropics/skills" in text
    assert "addyosmani/agent-skills" in text


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


def test_render_rejects_corrupted_merged_payload(tmp_path):
    """A truncated or hand-edited merged.json must exit 1 with a message,
    not blow up with KeyError/TypeError."""
    from src.rank.cache import Cache

    cfg = _write_config(tmp_path)
    cache = Cache(tmp_path / "data" / "cache")

    cache.write_json("merged.json", 5)                 # bare scalar
    assert cli.main(["--config", str(cfg), "render"]) == 1

    cache.write_json("merged.json", {"records": {}})   # wrapper without a date
    assert cli.main(["--config", str(cfg), "render"]) == 1

    cache.write_json("merged.json", {"merged_on": "not-a-date", "records": {}})
    assert cli.main(["--config", str(cfg), "render"]) == 1
