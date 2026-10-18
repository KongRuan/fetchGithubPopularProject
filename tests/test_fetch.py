# tests/test_fetch.py
import pytest
import requests

from src.rank.cache import Cache
from src.rank.config import Settings
from src.rank.fetch import DELAY_SECONDS, FetchError, TrendingFetcher, page_key


def _resp(status=200, text="<html/>"):
    """A real requests.Response so raise_for_status()/text behave as in production."""
    response = requests.Response()
    response.status_code = status
    response.url = "https://github.com/trending"
    response._content = text.encode("utf-8")
    return response


def test_page_key_is_scoped_by_date_and_period():
    assert page_key("monthly", "python", "2026-10-18") == \
        "pages/2026-10-18/trending_monthly_python.html"
    assert page_key("weekly", None, "2026-10-18") == \
        "pages/2026-10-18/trending_weekly_all.html"


def test_retry_then_success():
    class Resp:
        status_code = 200
        text = "<html/>"
        def raise_for_status(self):
            return None

    class Flaky:
        def __init__(self):
            self.calls = 0
        def get(self, url, timeout, headers=None):
            self.calls += 1
            if self.calls < 3:
                raise OSError("boom")
            return Resp()

    session = Flaky()
    fetcher = TrendingFetcher(session=session, sleep=lambda _s: None)
    assert fetcher.get("http://x") == "<html/>"
    assert session.calls == 3


def test_gives_up_after_retries():
    class AlwaysDown:
        def get(self, url, timeout, headers=None):
            raise OSError("boom")

    fetcher = TrendingFetcher(session=AlwaysDown(), sleep=lambda _s: None)
    with pytest.raises(FetchError):
        fetcher.get("http://x", retries=3)


def test_builds_expected_urls(tmp_path):
    settings = Settings(root=tmp_path, languages=("python", "c%23"))
    urls = list(TrendingFetcher(session=None, sleep=lambda _s: None)
                .trending_urls(settings))
    assert urls == [
        ("daily", None, "https://github.com/trending?since=daily"),
        ("daily", "python", "https://github.com/trending/python?since=daily"),
        ("daily", "c%23", "https://github.com/trending/c%23?since=daily"),
        ("weekly", None, "https://github.com/trending?since=weekly"),
        ("weekly", "python", "https://github.com/trending/python?since=weekly"),
        ("weekly", "c%23", "https://github.com/trending/c%23?since=weekly"),
        ("monthly", None, "https://github.com/trending?since=monthly"),
        ("monthly", "python", "https://github.com/trending/python?since=monthly"),
        ("monthly", "c%23", "https://github.com/trending/c%23?since=monthly"),
    ]


def test_backoff_is_exponential_and_skips_the_final_sleep():
    sleeps = []

    class AlwaysDown:
        def get(self, url, timeout, headers=None):
            raise OSError("boom")

    fetcher = TrendingFetcher(session=AlwaysDown(), sleep=sleeps.append, retries=3)
    with pytest.raises(FetchError):
        fetcher.get("http://x")
    assert sleeps == [1.0, 2.0]


def test_fetch_error_chains_the_original_cause():
    class AlwaysDown:
        def get(self, url, timeout, headers=None):
            raise OSError("boom")

    fetcher = TrendingFetcher(session=AlwaysDown(), sleep=lambda _s: None, retries=2)
    with pytest.raises(FetchError) as excinfo:
        fetcher.get("http://x")
    assert isinstance(excinfo.value.__cause__, OSError)
    assert "OSError" in str(excinfo.value)


def test_not_found_is_not_retried():
    calls = []

    class NotFound:
        def get(self, url, timeout, headers=None):
            calls.append(url)
            return _resp(404, "nope")

    fetcher = TrendingFetcher(session=NotFound(), sleep=lambda _s: None, retries=3)
    with pytest.raises(FetchError) as excinfo:
        fetcher.get("http://x")
    assert len(calls) == 1
    assert "404" in str(excinfo.value)


def test_programming_errors_are_not_masked_as_network_failures():
    """A TypeError must surface on attempt 1, not be retried three times and
    rebranded as a network failure.

    Raised inside the body (a signature mismatch raises during argument
    binding instead, before the body can record the call)."""
    calls = []

    class Broken:
        def get(self, url, timeout, headers=None):
            calls.append(url)
            raise TypeError("bug in caller")

    fetcher = TrendingFetcher(session=Broken(), sleep=lambda _s: None, retries=3)
    with pytest.raises(TypeError):
        fetcher.get("http://x")
    assert len(calls) == 1


def test_signature_mismatch_propagates_type_error():
    class BadSignature:
        def get(self, url, timeout):  # deliberately missing headers=
            raise AssertionError("body must never run")

    fetcher = TrendingFetcher(session=BadSignature(), sleep=lambda _s: None, retries=3)
    with pytest.raises(TypeError):
        fetcher.get("http://x")


def test_fetch_all_resumes_within_the_same_day(tmp_path):
    """Date-scoped keys are the resume mechanism: a same-day re-run must make
    zero network calls and sleep zero times."""
    settings = Settings(root=tmp_path, languages=("python", "c%23"))
    cache = Cache(tmp_path / "cache")
    calls, sleeps = [], []

    class OK:
        def get(self, url, timeout, headers=None):
            calls.append(url)
            return _resp(200, f"<html>{url}</html>")

    fetcher = TrendingFetcher(session=OK(), sleep=sleeps.append)
    day = "2026-10-18"

    keys = fetcher.fetch_all(settings, cache, day)
    assert len(keys) == 9
    assert len(calls) == 9
    assert sleeps == [DELAY_SECONDS] * 9

    calls.clear()
    sleeps.clear()
    assert fetcher.fetch_all(settings, cache, day) == keys
    assert calls == []
    assert sleeps == []

    # A new day is a fresh key, so it refetches.
    calls.clear()
    fetcher.fetch_all(settings, cache, "2026-10-19")
    assert len(calls) == 9


def test_fetch_all_isolates_a_single_failing_page(tmp_path):
    settings = Settings(root=tmp_path, languages=("python", "c%23"))
    cache = Cache(tmp_path / "cache")

    class HalfDown:
        def get(self, url, timeout, headers=None):
            if "c%23" in url:
                raise OSError("boom")
            return _resp(200, "<html/>")

    fetcher = TrendingFetcher(session=HalfDown(), sleep=lambda _s: None, retries=1)
    keys = fetcher.fetch_all(settings, cache, "2026-10-18")
    # 3 windows x (global + python) survive; the broken slug is skipped.
    assert len(keys) == 6
    assert all("c%23" not in key for key in keys)
