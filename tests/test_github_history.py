"""GitHub's privacy-safe star history as the primary missing-gain source."""
from datetime import date, datetime, timezone

import requests

from src.rank import estimate as est
from src.rank.cache import Cache


def _week(iso_day, days):
    return {"week": int(datetime.fromisoformat(iso_day).replace(
        tzinfo=timezone.utc).timestamp()), "days": days, "total": sum(days)}


def _rec(repo, m30=None):
    return {"repo": repo, "stars": 100, "m30": m30,
            "w7": None, "d1": None, "desc": "", "lang": "",
            "source": "trending"}


def test_window_gain_sums_only_dates_including_both_edges():
    rows = [
        _week("2026-10-18", [7, 0, 0, 0, 0, 0, 0]),
        _week("2026-10-11", [1, 2, 3, 4, 5, 6, 7]),
    ]
    assert est.github_window_gain(rows, date(2026, 10, 15),
                                  date(2026, 10, 18)) == 25


def test_window_gain_requires_complete_window_including_zero_star_days():
    assert est.github_window_gain(
        [_week("2026-10-18", [7, 0, 0, 0, 0, 0, 0])],
        date(2026, 10, 17), date(2026, 10, 18),
    ) is None
    assert est.github_window_gain(
        [_week("2026-10-11", [0, 0, 0, 0, 0, 0, 0])],
        date(2026, 10, 15), date(2026, 10, 16),
    ) == 0


def test_fetch_github_history_uses_token_and_own_cache(tmp_path):
    cache = Cache(tmp_path)
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [_week("2026-10-18", [1, 2, 3, 4, 5, 6, 7])]

    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    args = ("octo", "repo", cache, "2026-10-18")
    first = est.fetch_github_history(*args, session=Session(), token="test-token")
    second = est.fetch_github_history(*args, session=Session(), token="test-token")
    assert second == first
    assert len(calls) == 1
    assert calls[0][0] == "https://api.github.com/repos/octo/repo/stargazers/history"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-token"
    assert calls[0][1]["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
    assert cache.read_json("github_history/2026-10-18/octo@repo.json") == first


def test_github_rate_limit_skips_future_network_but_uses_cache(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("github_history/2026-10-18/cached@repo.json", [
        _week("2026-10-18", [1, 0, 0, 0, 0, 0, 0]),
    ])
    records = {"limited/repo": _rec("limited/repo"),
               "cached/repo": _rec("cached/repo")}
    calls = []

    def limited(*args, **kwargs):
        calls.append(args)
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError("rate limit", response=response)

    est.fill_github_estimates(
        records, date_from=date(2026, 10, 18), date_to=date(2026, 10, 18),
        history=limited, cache=cache, day="2026-10-18",
    )
    assert len(calls) == 1
    assert records["limited/repo"]["m30"] is None
    assert records["cached/repo"]["m30"] == 1
    assert records["cached/repo"]["source"] == "est-github"
