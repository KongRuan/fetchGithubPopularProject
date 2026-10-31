# tests/test_skills.py
from datetime import date

from src.rank.cache import Cache
from src.rank.config import Settings
from src.rank import skills as sk


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    """Stubs the Search API: every query returns the same two rows."""

    def __init__(self):
        self.queries = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.queries.append(params["q"])
        return _Response({"items": [
            {"full_name": "anthropics/skills", "stargazers_count": 185_000,
             "description": "Public repository for Agent Skills",
             "language": None, "created_at": "2025-09-22T00:00:00Z"},
            {"full_name": "newbie/skill", "stargazers_count": 2500,
             "description": "Brand new agent skill",
             "language": "Python", "created_at": "2026-10-20T00:00:00Z"},
        ]})


def _settings(root):
    return Settings(root=root, period_days=30, top_n=2,
                    enable_ossinsight_estimate=False)


def test_search_runs_scoped_and_unscoped_queries():
    session = _Session()
    rows = sk.search_skill_repos(session, date_from=date(2026, 10, 2),
                                 sleep=lambda _s: None)
    assert len(rows) == 2                                    # deduped
    assert len(session.queries) == 2 * len(sk.SKILL_QUERIES)
    assert sum("created:>2026-10-02" in q for q in session.queries) == (
        len(sk.SKILL_QUERIES))
    assert {r["repo"] for r in rows} == {"anthropics/skills", "newbie/skill"}
    assert all("created_at" in r for r in rows)


def test_rate_limited_queries_are_skipped_not_fatal():
    class Limited(_Session):
        def get(self, url, headers=None, params=None, timeout=None):
            return _Response({"message": "rate limit"}, status_code=403)

    assert sk.search_skill_repos(Limited(), date_from=date(2026, 10, 2),
                                 sleep=lambda _s: None) == []


def test_repo_born_inside_the_window_uses_its_total_as_gain(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("skills/2026-11-01/candidates_v2_30d.json", [
        {"repo": "newbie/skill", "stars": 2500, "desc": "x", "lang": "",
         "created_at": "2026-10-20"},
    ])
    recs = sk.build_skill_records(_settings(tmp_path), cache, "2026-11-01",
                                  date(2026, 11, 1), session=object(),
                                  sleep=lambda _s: None)
    assert recs["newbie/skill"]["m30"] == 2500
    assert recs["newbie/skill"]["source"] == "search-new"


def test_old_repo_without_a_baseline_cannot_rank(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("skills/2026-11-01/candidates_v2_30d.json", [
        {"repo": "old/skill", "stars": 90_000, "desc": "", "lang": "",
         "created_at": "2025-01-01"},
    ])
    recs = sk.build_skill_records(_settings(tmp_path), cache, "2026-11-01",
                                  date(2026, 11, 1), session=object(),
                                  sleep=lambda _s: None)
    assert recs["old/skill"]["m30"] is None
    assert recs["old/skill"]["source"] == "est-failed"


def test_old_repo_gain_comes_from_the_ledger(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("skills/2026-11-01/candidates_v2_30d.json", [
        {"repo": "old/skill", "stars": 1000, "desc": "", "lang": "",
         "created_at": "2025-01-01"},
    ])
    # A reading exactly at the window start (2026-10-02) is the baseline.
    cache.write_json("stars/2026-10-02.json", {"old/skill": 600})
    recs = sk.build_skill_records(_settings(tmp_path), cache, "2026-11-01",
                                  date(2026, 11, 1), session=object(),
                                  sleep=lambda _s: None)
    assert recs["old/skill"]["m30"] == 400
    assert recs["old/skill"]["source"] == "snapshot"


def test_a_reading_inside_the_window_is_not_a_baseline(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("skills/2026-11-01/candidates_v2_30d.json", [
        {"repo": "old/skill", "stars": 1000, "desc": "", "lang": "",
         "created_at": "2025-01-01"},
    ])
    # 2026-10-20 is inside the window; using it would understate the gain.
    cache.write_json("stars/2026-10-20.json", {"old/skill": 900})
    recs = sk.build_skill_records(_settings(tmp_path), cache, "2026-11-01",
                                  date(2026, 11, 1), session=object(),
                                  sleep=lambda _s: None)
    assert recs["old/skill"]["m30"] is None


def test_every_run_appends_to_the_star_ledger(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("skills/2026-11-01/candidates_v2_30d.json", [
        {"repo": "old/skill", "stars": 1000, "desc": "", "lang": "",
         "created_at": "2025-01-01"},
    ])
    sk.build_skill_records(_settings(tmp_path), cache, "2026-11-01",
                           date(2026, 11, 1), session=object(),
                           sleep=lambda _s: None)
    assert cache.read_json("stars/2026-11-01.json") == {"old/skill": 1000}


def test_yesterdays_pool_is_reused_without_a_network_call(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("skills/2026-10-31/candidates_v2_30d.json", [
        {"repo": "a/b", "stars": 10, "desc": "", "lang": "",
         "created_at": "2026-10-30"},
    ])

    class NoNetwork:
        def get(self, *a, **kw):
            raise AssertionError("no network when a recent pool is cached")

    recs = sk.build_skill_records(_settings(tmp_path), cache, "2026-11-01",
                                  date(2026, 11, 1), session=NoNetwork(),
                                  sleep=lambda _s: None)
    assert set(recs) == {"a/b"}


def test_an_out_of_date_merge_must_not_leak_into_this_window(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("skills/2026-11-01/candidates_v2_30d.json", [
        {"repo": "a/b", "stars": 400, "desc": "", "lang": "",
         "created_at": "2026-10-30"},
    ])
    cache.write_json("merged.json", {
        "merged_on": "2026-10-18",               # a different reporting window
        "records": {"a/b": {"repo": "a/b", "m30": 999_999, "source": "trending"}},
    })
    recs = sk.build_skill_records(_settings(tmp_path), cache, "2026-11-01",
                                  date(2026, 11, 1), session=object(),
                                  sleep=lambda _s: None)
    assert recs["a/b"]["m30"] == 400
