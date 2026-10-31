"""Fetch cached Chinese one-line summaries for repository descriptions.

The pipeline itself stays offline-capable: every summary is written to the
date-scoped cache under ``desc_zh/<day>/``. A same-day re-render never calls
the network again; a new day refetches. The upstream service is OpenAI's
gpt-4o-mini via a local endpoint — descriptions are public README-free one
liners, well inside the model's strength, and failures degrade gracefully to
the original English description.
"""
from __future__ import annotations

import logging
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
DELAY_SECONDS = 0.35


def _key(day: str, repo: str) -> str:
    return f"desc_zh/{day}/{repo.replace('/', '@')}.json"


def _prompt(description: str, repo: str) -> str:
    return (
        "你是开源项目简介的中文译者。把下面的 GitHub 项目一句话简介翻译成"
        "简体中文，保留专有名词英文，不要添加解释、不要加引号，"
        "输出只有译文本身。仓库：" + repo + "\n简介：" + description
    )


def translate_description(
    repo: str,
    description: str,
    *,
    session=None,
    cache: Cache,
    day: str,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 2,
) -> str | None:
    """Return the Chinese summary for ``description``, or None on failure."""
    if not description:
        return None
    key = _key(day, repo)
    cached = cache.read_json(key)
    if isinstance(cached, dict) and isinstance(cached.get("zh"), str):
        return cached["zh"]

    payload = {
        "model": MODEL,
        "input": _prompt(description, repo),
    }
    http = (session or requests).post if session is not None else requests.post
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = http(ENDPOINT, json=payload, headers=HEADERS, timeout=30)
            response.raise_for_status()
            body = response.json()
            text = next(
                (content.get("text", "").strip()
                 for item in body.get("output", [])
                 for content in item.get("content", [])
                 if content.get("type") == "output_text"),
                "",
            )
            if text:
                cache.write_json(key, {"zh": text})
                return text
            last = ValueError("empty translation")
        except (requests.RequestException, OSError, ValueError,
                KeyError, IndexError) as exc:
            last = exc
            log.warning("translate attempt %d/%d failed for %s: %s",
                        attempt, retries, repo, exc)
            if attempt < retries:
                sleep(DELAY_SECONDS)
    log.warning("no Chinese summary for %s: %s", repo, last)
    return None


def fill_chinese_summaries(records: Iterable[dict], *, cache: Cache, day: str) -> None:
    """Attach ``desc_zh`` to records that have an English description.

    Callers pass only the rows the report will actually show: a translation
    costs one model call, and the archived JSON is not worth 800 of them.
    """
    with requests.Session() as session:
        for rec in records:
            desc = rec.get("desc") or ""
            if not desc:
                continue
            zh = translate_description(
                rec["repo"], desc, session=session, cache=cache, day=day)
            if zh:
                rec["desc_zh"] = zh
