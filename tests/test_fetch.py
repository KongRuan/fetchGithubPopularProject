# tests/test_fetch.py
import pytest

from src.rank.config import Settings
from src.rank.fetch import FetchError, TrendingFetcher, page_key


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
