"""Offline tests for degraded translation service behavior."""
import requests

from src.rank.cache import Cache
from src.rank.translate import fill_chinese_summaries


def test_translation_outage_stops_requests_but_preserves_cached_summaries(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_TRANSLATOR_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    cache = Cache(tmp_path)
    cache.write_json("desc_zh/2026-10-18/cached@repo.json", {"zh": "已缓存的简介"})
    records = [{"repo": f"bad/r{i}", "desc": "English"} for i in range(3)]
    records.append({"repo": "cached/repo", "desc": "English"})
    calls = []

    def timed_out(self, url, **kwargs):
        calls.append(url)
        raise requests.ReadTimeout("local model stalled")

    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: (
        _ for _ in ()).throw(requests.ReadTimeout("MyMemory unavailable")))
    monkeypatch.setattr(requests.Session, "post", timed_out)
    monkeypatch.setattr("src.rank.translate.time.sleep", lambda _: None)
    fill_chinese_summaries(records, cache=cache, day="2026-10-18")

    assert len(calls) == 4  # two retries each for the first two repositories
    assert "desc_zh" not in records[2]
    assert records[3]["desc_zh"] == "已缓存的简介"


def test_azure_free_tier_translation_precedes_local_and_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "azure-test-key")
    monkeypatch.setenv("AZURE_TRANSLATOR_REGION", "eastus")
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"translations": [{"text": "用于测试的中文简介"}]}]

    def post(self, url, **kwargs):
        calls.append((url, kwargs))
        if url.startswith("http://127.0.0.1"):
            raise AssertionError("local model should not run")
        return Response()

    monkeypatch.setattr(requests.Session, "post", post)
    cache = Cache(tmp_path)
    records = [{"repo": "owner/repo", "desc": "A tool for testing"}]
    fill_chinese_summaries(records, cache=cache, day="2026-10-18")
    assert records[0]["desc_zh"] == "用于测试的中文简介"
    assert calls[0][0] == "https://api.cognitive.microsofttranslator.com/translate"
    assert calls[0][1]["params"] == {"api-version": "3.0", "to": "zh-Hans"}
    assert calls[0][1]["headers"]["Ocp-Apim-Subscription-Region"] == "eastus"
    assert calls[0][1]["json"] == [{"Text": "A tool for testing"}]
    fill_chinese_summaries([{"repo": "owner/repo", "desc": "A tool for testing"}],
                           cache=cache, day="2026-10-18")
    assert len(calls) == 1


def test_azure_outage_falls_back_to_google_and_decodes_html(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "azure-test-key")
    monkeypatch.setenv("GOOGLE_TRANSLATE_API_KEY", "google-test-key")
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"translations": [{"translatedText": "工具 &amp; 文档"}]}}

    def post(self, url, **kwargs):
        calls.append((url, kwargs))
        if "microsofttranslator" in url:
            raise requests.ReadTimeout("azure down")
        if url.startswith("http://127.0.0.1"):
            raise AssertionError("local model should not run")
        return Response()

    monkeypatch.setattr(requests.Session, "post", post)
    records = [{"repo": "owner/repo", "desc": "Tools & docs"}]
    fill_chinese_summaries(records, cache=Cache(tmp_path), day="2026-10-18")
    assert records[0]["desc_zh"] == "工具 & 文档"
    assert [url for url, _ in calls] == [
        "https://api.cognitive.microsofttranslator.com/translate",
        "https://translation.googleapis.com/language/translate/v2",
    ]
    assert calls[1][1]["params"] == {"key": "google-test-key"}
    assert calls[1][1]["json"] == {"q": "Tools & docs", "target": "zh-CN", "format": "text"}


def test_cloud_failures_disable_only_failed_provider_and_use_local(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "azure-test-key")
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"output": [{"content": [{"type": "output_text", "text": "本地翻译"}]}]}

    def post(self, url, **kwargs):
        calls.append(url)
        if "microsofttranslator" in url:
            raise requests.ReadTimeout("azure down")
        return Response()

    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: (
        _ for _ in ()).throw(requests.ReadTimeout("MyMemory unavailable")))
    monkeypatch.setattr(requests.Session, "post", post)
    records = [{"repo": f"owner/{i}", "desc": "A repo"} for i in range(3)]
    fill_chinese_summaries(records, cache=Cache(tmp_path), day="2026-10-18")
    assert [r["desc_zh"] for r in records] == ["本地翻译"] * 3
    assert sum("microsofttranslator" in url for url in calls) == 2
    assert sum("127.0.0.1" in url for url in calls) == 3


def test_mymemory_translates_without_credentials_and_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_TRANSLATOR_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"responseStatus": 200,
                    "responseData": {"translatedText": "工具 &amp; 文档"}}

    def get(self, url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    def post(self, url, **kwargs):
        raise AssertionError("local model should not run")

    monkeypatch.setattr(requests.Session, "get", get)
    monkeypatch.setattr(requests.Session, "post", post)
    cache = Cache(tmp_path)
    records = [{"repo": "owner/repo", "desc": "Tools & docs"}]
    fill_chinese_summaries(records, cache=cache, day="2026-10-18")
    assert records[0]["desc_zh"] == "工具 & 文档"
    assert calls == [("https://api.mymemory.translated.net/get",
                      {"params": {"q": "Tools & docs", "langpair": "en|zh-CN"},
                       "timeout": 10})]
    fill_chinese_summaries([{"repo": "owner/repo", "desc": "Tools & docs"}],
                           cache=cache, day="2026-10-18")
    assert len(calls) == 1


def test_mymemory_quota_warning_is_not_cached_or_displayed(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_TRANSLATOR_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    get_calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"responseStatus": 429,
                    "responseData": {"translatedText": "MYMEMORY WARNING: quota exhausted"}}

    def get(self, url, **kwargs):
        get_calls.append(url)
        return Response()

    class LocalResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"output": [{"content": [{"type": "output_text",
                                                "text": "本地备用译文"}]}]}

    monkeypatch.setattr(requests.Session, "get", get)
    monkeypatch.setattr(requests.Session, "post", lambda *args, **kwargs: LocalResponse())
    cache = Cache(tmp_path)
    records = [{"repo": f"owner/{i}", "desc": "Description"} for i in range(3)]
    fill_chinese_summaries(records, cache=cache, day="2026-10-18")
    assert [rec["desc_zh"] for rec in records] == ["本地备用译文"] * 3
    assert len(get_calls) == 2
    assert cache.read_json("desc_zh/2026-10-18/owner@0.json") == {"zh": "本地备用译文"}


def test_mymemory_skips_overlong_utf8_input_and_uses_local(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_TRANSLATOR_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: (
        _ for _ in ()).throw(AssertionError("MyMemory's 500-byte limit exceeded")))

    class LocalResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"output": [{"content": [{"type": "output_text",
                                                "text": "本地完整译文"}]}]}

    monkeypatch.setattr(requests.Session, "post", lambda *args, **kwargs: LocalResponse())
    records = [{"repo": "owner/large", "desc": "甲" * 200}]
    fill_chinese_summaries(records, cache=Cache(tmp_path), day="2026-10-18")
    assert records[0]["desc_zh"] == "本地完整译文"
