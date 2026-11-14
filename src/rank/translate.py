"""Translate repository descriptions through cloud, anonymous, or local APIs.

Successful results share the existing date-scoped ``desc_zh/<day>/`` cache.
"""
from __future__ import annotations

import html
import logging
import os
import time
from collections.abc import Iterable
from typing import Callable

import requests

from .cache import Cache

log = logging.getLogger(__name__)

ENDPOINT = "http://127.0.0.1:3425/v1/responses"
HEADERS = {
    "Authorization": "Bearer magpie",
    "x-openai-actor-authorization": "magpie",
}
MODEL = "gpt-4o-mini"
AZURE_ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
GOOGLE_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"
MYMEMORY_MAX_BYTES = 500
DELAY_SECONDS = 0.35
MAX_CONSECUTIVE_FAILURES = 2


def _key(day: str, repo: str) -> str:
    return f"desc_zh/{day}/{repo.replace('/', '@')}.json"


def _prompt(description: str, repo: str) -> str:
    return (
        "你是开源项目简介的中文译者。把下面的 GitHub 项目一句话简介翻译成"
        "简体中文，保留专有名词英文，不要添加解释、不要加引号，"
        "输出只有译文本身。仓库：" + repo + "\n简介：" + description
    )


def _translate_azure(http, description: str, key: str, region: str) -> str:
    headers = {"Ocp-Apim-Subscription-Key": key,
               "Content-Type": "application/json; charset=UTF-8"}
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region
    response = http(AZURE_ENDPOINT,
                    params={"api-version": "3.0", "to": "zh-Hans"},
                    json=[{"Text": description}], headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()[0]["translations"][0]["text"].strip()


def _translate_google(http, description: str, key: str) -> str:
    response = http(GOOGLE_ENDPOINT, params={"key": key},
                    json={"q": description, "target": "zh-CN", "format": "text"},
                    timeout=10)
    response.raise_for_status()
    return html.unescape(response.json()["data"]["translations"][0]
                         ["translatedText"]).strip()


def _translate_mymemory(http, description: str) -> str:
    response = http(MYMEMORY_ENDPOINT,
                    params={"q": description, "langpair": "en|zh-CN"}, timeout=10)
    response.raise_for_status()
    body = response.json()
    # MyMemory can return an HTTP 200 carrying a quota warning as translatedText.
    if body["responseStatus"] != 200:
        raise ValueError("MyMemory did not return a translation")
    text = html.unescape(body["responseData"]["translatedText"]).strip()
    if text.upper().startswith("MYMEMORY WARNING:"):
        raise ValueError("MyMemory returned a quota warning")
    return text


def _translate_local(http, description: str, repo: str, *,
                     sleep: Callable[[float], None], retries: int) -> str:
    payload = {"model": MODEL, "input": _prompt(description, repo)}
    for attempt in range(1, retries + 1):
        try:
            response = http(ENDPOINT, json=payload, headers=HEADERS, timeout=30)
            response.raise_for_status()
            text = next(
                (content.get("text", "").strip()
                 for item in response.json().get("output", [])
                 for content in item.get("content", [])
                 if content.get("type") == "output_text"), "")
            if text:
                return text
            raise ValueError("empty translation")
        except (requests.RequestException, OSError, ValueError,
                KeyError, IndexError, TypeError) as exc:
            log.warning("translate attempt %d/%d failed for %s: %s",
                        attempt, retries, repo, exc)
            if attempt == retries:
                raise
            sleep(DELAY_SECONDS)
    return ""  # retries=0


def translate_description(
    repo: str,
    description: str,
    *,
    session=None,
    cache: Cache,
    day: str,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 2,
    allow_network: bool = True,
    failures: dict[str, int] | None = None,
) -> str | None:
    """Use configured cloud APIs, anonymous MyMemory, then local AI.

    Each source has an independent circuit breaker for a render; cached
    translations are always checked before any network request.
    """
    if not description:
        return None
    key = _key(day, repo)
    cached = cache.read_json(key)
    if isinstance(cached, dict) and isinstance(cached.get("zh"), str):
        return cached["zh"]
    if not allow_network:
        return None

    http = session.post if session is not None else requests.post
    get = session.get if session is not None else requests.get
    state = failures if failures is not None else {}
    azure_key = os.environ.get("AZURE_TRANSLATOR_KEY", "")
    google_key = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")
    sources = []
    if azure_key:
        sources.append(("azure", lambda: _translate_azure(
            http, description, azure_key, os.environ.get("AZURE_TRANSLATOR_REGION", ""))))
    if google_key:
        sources.append(("google", lambda: _translate_google(http, description, google_key)))
    # The anonymous API limits each input to 500 UTF-8 bytes. Never truncate a
    # description into a misleading summary or count a long input as an outage.
    if len(description.encode("utf-8")) <= MYMEMORY_MAX_BYTES:
        sources.append(("mymemory", lambda: _translate_mymemory(get, description)))
    sources.append(("local", lambda: _translate_local(
        http, description, repo, sleep=sleep, retries=retries)))

    for name, translate in sources:
        if state.get(name, 0) >= MAX_CONSECUTIVE_FAILURES:
            continue
        try:
            text = translate()
            if not text:
                raise ValueError("empty translation")
        except (requests.RequestException, OSError, ValueError,
                KeyError, IndexError, TypeError) as exc:
            state[name] = state.get(name, 0) + 1
            # Don't log the Google request exception: its URL may contain an API key.
            log.warning("%s translation failed for %s (%s)",
                        name, repo, type(exc).__name__)
            if state[name] == MAX_CONSECUTIVE_FAILURES:
                log.warning("%s translation unavailable; skipping it for remaining repositories",
                            name)
            continue
        cache.write_json(key, {"zh": text})
        state[name] = 0
        return text
    return None


def fill_chinese_summaries(records: Iterable[dict], *, cache: Cache, day: str) -> None:
    """Attach cached or translated summaries to the report's ranked records."""
    failures: dict[str, int] = {}
    with requests.Session() as session:
        for rec in records:
            desc = rec.get("desc") or ""
            if not desc:
                continue
            zh = translate_description(rec["repo"], desc, session=session,
                                       cache=cache, day=day, failures=failures)
            if zh:
                rec["desc_zh"] = zh
