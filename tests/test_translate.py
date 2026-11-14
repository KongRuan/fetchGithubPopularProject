"""Offline tests for degraded translation service behavior."""
import requests

from src.rank.cache import Cache
from src.rank.translate import fill_chinese_summaries


def test_translation_outage_stops_requests_but_preserves_cached_summaries(tmp_path, monkeypatch):
    cache = Cache(tmp_path)
    cache.write_json("desc_zh/2026-10-18/cached@repo.json", {"zh": "已缓存的简介"})
    records = [{"repo": f"bad/r{i}", "desc": "English"} for i in range(3)]
    records.append({"repo": "cached/repo", "desc": "English"})
    calls = []

    def timed_out(self, url, **kwargs):
        calls.append(url)
        raise requests.ReadTimeout("local model stalled")

    monkeypatch.setattr(requests.Session, "post", timed_out)
    monkeypatch.setattr("src.rank.translate.time.sleep", lambda _: None)
    fill_chinese_summaries(records, cache=cache, day="2026-10-18")

    assert len(calls) == 4  # two retries each for the first two repositories
    assert "desc_zh" not in records[2]
    assert records[3]["desc_zh"] == "已缓存的简介"
