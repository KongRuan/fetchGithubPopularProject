# tests/test_estimate.py
from datetime import date

import pytest

from src.rank import estimate as est
from src.rank.cache import Cache
from src.rank.merge import SOURCE_EST_DAY, SOURCE_EST_WEEK


def _rec(repo, m30=None, source="trending"):
    return {"repo": repo, "stars": 100, "m30": m30, "w7": None, "d1": None,
            "desc": "", "lang": "", "source": source}


def test_delta_none_when_fewer_than_two_points():
    assert est.history_delta([]) is None
    assert est.history_delta([{"date": "2026-10-01", "stargazers": "5"}]) is None


def test_delta_is_last_minus_first():
    rows = [
        {"date": "2026-09-18", "stargazers": "1,000"},
        {"date": "2026-09-25", "stargazers": "1,500"},
        {"date": "2026-10-12", "stargazers": "4,000"},
    ]
    assert est.history_delta(rows) == 3000


def test_history_url_shape():
    url = est.history_url("octo", "hi", "day", date(2026, 9, 18), date(2026, 10, 18))
    assert url == (
        "https://api.ossinsight.io/v1/repos/octo/hi/stargazers/history/"
        "?per=day&from=2026-09-18&to=2026-10-18"
    )


def test_fill_only_touches_records_missing_m30():
    calls = []

    def fake_history(owner, name, period, date_from, date_to, cache, day):
        calls.append((owner, name, period))
        return [{"date": "a", "stargazers": "10"}, {"date": "b", "stargazers": "990"}]

    records = {
        "has/gain": _rec("has/gain", m30=42),
        "needs/gain": _rec("needs/gain"),
    }
    est.fill_estimates(records, period_days=30, enable=True,
                       date_from=date(2026, 9, 18), date_to=date(2026, 10, 18),
                       history=fake_history, cache=None, day="2026-10-18")
    assert calls == [("needs", "gain", "week")]
    assert records["needs/gain"]["m30"] == 980
    assert records["needs/gain"]["source"] == SOURCE_EST_WEEK
    assert records["has/gain"]["m30"] == 42


def test_fill_marks_failure_without_raising():
    def boom(*args, **kwargs):
        raise OSError("network down")

    records = {"bad/repo": _rec("bad/repo")}
    est.fill_estimates(records, period_days=30, enable=True,
                       date_from=date(2026, 9, 18), date_to=date(2026, 10, 18),
                       history=boom, cache=None, day="2026-10-18")
    assert records["bad/repo"]["m30"] is None
    assert records["bad/repo"]["source"] == "est-failed"


def test_revalidate_uses_daily_series_for_top_estimates():
    seen = []

    def fake_history(owner, name, period, date_from, date_to, cache, day):
        seen.append(period)
        return [{"date": "a", "stargazers": "0"}, {"date": "b", "stargazers": "777"}]

    top = [_rec("top/repo", m30=9000, source="est-week")]
    est.revalidate(top, date_from=date(2026, 9, 18), date_to=date(2026, 10, 18),
                   history=fake_history, cache=None, day="2026-10-18")
    assert seen == ["day"]
    assert top[0]["m30"] == 777
    assert top[0]["source"] == SOURCE_EST_DAY


def test_disabled_estimate_is_a_no_op():
    calls = []

    def fake_history(*args, **kwargs):
        calls.append(args)
        return []

    records = {"x/y": _rec("x/y")}
    est.fill_estimates(records, period_days=30, enable=False,
                       date_from=date(2026, 9, 18), date_to=date(2026, 10, 18),
                       history=fake_history, cache=None, day="2026-10-18")
    assert calls == []
    assert records["x/y"]["m30"] is None


# --- retry behavior (authorized deviation) -----------------------------------
# All offline: requests.get is stubbed, sleeps are recorded, never performed.

class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"rows": self._rows}}


def _recorder():
    slept = []
    return slept.append, slept


def test_fetch_history_retries_transient_failure_then_succeeds(monkeypatch):
    attempts = []

    def flaky_get(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 3:
            raise OSError("connection reset")
        return _Resp([{"date": "a", "stargazers": "10"},
                      {"date": "b", "stargazers": "990"}])

    monkeypatch.setattr(est.requests, "get", flaky_get)
    sleep, slept = _recorder()
    rows = est.fetch_history("octo", "hi", "week", date(2026, 9, 18),
                             date(2026, 10, 18), None, "2026-10-18",
                             sleep=sleep)
    assert len(attempts) == 3
    assert rows == [{"date": "a", "stargazers": "10"},
                    {"date": "b", "stargazers": "990"}]
    assert slept == [est.DELAY_SECONDS, est.DELAY_SECONDS * 2]


def test_fetch_history_gives_up_and_raises_fetch_error(monkeypatch):
    calls = []

    def dead_get(url, **kwargs):
        calls.append(url)
        raise OSError("network down")

    monkeypatch.setattr(est.requests, "get", dead_get)
    sleep, slept = _recorder()
    with pytest.raises(est.FetchError):
        est.fetch_history("octo", "hi", "week", date(2026, 9, 18),
                          date(2026, 10, 18), None, "2026-10-18",
                          sleep=sleep)
    assert len(calls) == 3  # default retries=3
    assert slept == [est.DELAY_SECONDS, est.DELAY_SECONDS * 2]


def test_fetch_history_returns_cache_without_touching_network(tmp_path, monkeypatch):
    cache = Cache(tmp_path)
    key = "est/2026-10-18/octo@hi_week_2026-09-18_2026-10-18.json"
    cached_rows = [{"date": "a", "stargazers": "7"}, {"date": "b", "stargazers": "17"}]
    cache.write_json(key, cached_rows)

    def forbidden_get(url, **kwargs):
        raise AssertionError("network touched on a cache hit")

    monkeypatch.setattr(est.requests, "get", forbidden_get)
    sleep, slept = _recorder()
    rows = est.fetch_history("octo", "hi", "week", date(2026, 9, 18),
                             date(2026, 10, 18), cache, "2026-10-18",
                             sleep=sleep)
    assert rows == cached_rows
    assert slept == []


def test_fetch_history_writes_cache_on_success(tmp_path, monkeypatch):
    cache = Cache(tmp_path)
    fetched = []

    def ok_get(url, **kwargs):
        fetched.append(url)
        return _Resp([{"date": "a", "stargazers": "1"},
                      {"date": "b", "stargazers": "21"}])

    monkeypatch.setattr(est.requests, "get", ok_get)
    sleep, _ = _recorder()
    first = est.fetch_history("octo", "hi", "week", date(2026, 9, 18),
                              date(2026, 10, 18), cache, "2026-10-18",
                              sleep=sleep)
    # Second call must be served from cache: forbid the network entirely.
    monkeypatch.setattr(est.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    second = est.fetch_history("octo", "hi", "week", date(2026, 9, 18),
                               date(2026, 10, 18), cache, "2026-10-18",
                               sleep=sleep)
    assert len(fetched) == 1
    assert first == second


def test_fill_marks_failure_when_fetch_gives_up(monkeypatch):
    def dead_get(url, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(est.requests, "get", dead_get)

    def failing_history(owner, name, period, date_from, date_to, cache, day):
        # Real fetch_history with sleeps neutralized; retry path still runs.
        return est.fetch_history(owner, name, period, date_from, date_to,
                                 cache, day, sleep=lambda _s: None)

    records = {"down/repo": _rec("down/repo")}
    est.fill_estimates(records, period_days=30, enable=True,
                       date_from=date(2026, 9, 18), date_to=date(2026, 10, 18),
                       history=failing_history, cache=None, day="2026-10-18")
    assert records["down/repo"]["m30"] is None
    assert records["down/repo"]["source"] == "est-failed"


def test_empty_series_is_est_failed_and_leaves_m30_none():
    """An unindexed repo yields zero rows; it must not become a fabricated +0."""
    records = {"new/repo": _rec("new/repo")}
    est.fill_estimates(records, period_days=30, enable=True,
                       date_from=date(2026, 9, 18), date_to=date(2026, 10, 18),
                       history=lambda *a: [], cache=None, day="2026-10-18")
    assert records["new/repo"]["m30"] is None
    assert records["new/repo"]["source"] == "est-failed"


def test_fetch_history_reuses_a_shared_session():
    """Without a session each request pays a fresh TLS handshake; that measured
    at ~4.9s/call and cost the first real run >10 minutes. cli binds one
    requests.Session for the whole estimate sweep."""
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"rows": [{"date": "a", "stargazers": "1"},
                                      {"date": "b", "stargazers": "9"}]}}

    class StubSession:
        def get(self, url, headers=None, timeout=None):
            calls.append((url, timeout))
            return Response()

    rows = est.fetch_history("o", "n", "week", date(2026, 9, 18),
                             date(2026, 10, 18), None, "2026-10-18",
                             session=StubSession(), sleep=lambda _s: None)
    assert rows == [{"date": "a", "stargazers": "1"},
                    {"date": "b", "stargazers": "9"}]
    assert len(calls) == 1
    assert calls[0][1] == 30


def test_rank_returns_live_references_into_records():
    """cli.step_merge polices revalidated estimates with a second
    apply_sanity_rules(records) pass; that only reaches the top list because
    rank hands back the very same dict objects."""
    from src.rank.merge import rank

    recs = {"a/b": _rec("a/b", m30=10), "c/d": _rec("c/d", m30=5)}
    top, rest = rank(recs, top_n=1)
    assert top[0] is recs["a/b"]
    assert rest[0] is recs["c/d"]


def test_revalidated_estimate_past_the_margin_is_dropped_end_to_end():
    """revalidate does not police the margin itself - the pipeline's second
    apply_sanity_rules pass does. Pin that contract so it can't silently rot."""
    from src.rank.merge import SOURCE_DROPPED, apply_sanity_rules

    rec = _rec("hot/repo", m30=500, source="est-week")
    rec["stars"] = 1000
    records = {"hot/repo": rec}

    def fake_history(owner, name, period, date_from, date_to, cache, day):
        return [{"date": "a", "stargazers": "0"},
                {"date": "b", "stargazers": "2000"}]

    est.revalidate([rec], date_from=date(2026, 9, 18),
                   date_to=date(2026, 10, 18), history=fake_history,
                   cache=None, day="2026-10-18")
    assert rec["m30"] == 2000
    assert rec["source"] == "est-day"

    apply_sanity_rules(records)
    assert rec["m30"] is None
    assert rec["source"] == SOURCE_DROPPED
