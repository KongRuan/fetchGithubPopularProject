"""Pure parsing of GitHub Trending HTML pages.

Logic ported from the PowerShell prototype that produced the validated
2026-10-18 ranking.
"""
from __future__ import annotations

import html as html_lib
import re

ARTICLE_RE = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
H2_RE = re.compile(r'<h2 class="h3 lh-condensed">(.*?)</h2>', re.S)
HREF_RE = re.compile(r'href="/([^"]+)"')
RETURN_TO_RE = re.compile(r"return_to=%2F([^\"&]+)")
STARS_LINK_RE = re.compile(r'href="[^"]+/stargazers"[^>]*>(.*?)</a>', re.S)
NUMBER_RE = re.compile(r"[\d,]+")
DESC_RE = re.compile(r'<p class="col-9[^"]*">(.*?)</p>', re.S)
LANG_RE = re.compile(r'itemprop="programmingLanguage">([^<]+)<')
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

GAIN_PATTERNS = {
    "monthly": re.compile(r"([\d,]+)\s*stars this month"),
    "weekly": re.compile(r"([\d,]+)\s*stars this week"),
    "daily": re.compile(r"([\d,]+)\s*stars today"),
}
GAIN_FIELD = {"monthly": "m30", "weekly": "w7", "daily": "d1"}
FIELDS = ("repo", "stars", "m30", "w7", "d1", "desc", "lang")


def _to_int(text: str) -> int:
    return int(text.replace(",", ""))


def _clean(fragment: str) -> str:
    text = TAG_RE.sub(" ", fragment)
    return WHITESPACE_RE.sub(" ", text).strip()


def _repo_id(block: str) -> str | None:
    match = H2_RE.search(block)
    if match:
        link = HREF_RE.search(match.group(1))
        if link:
            return link.group(1)
    fallback = RETURN_TO_RE.search(block)
    return fallback.group(1).replace("%2F", "/") if fallback else None


def parse_trending_html(page: str, period: str) -> list[dict]:
    """Return one record per repository row on a Trending page.

    ``period`` selects which "stars this N" figure is captured:
    ``monthly`` -> ``m30``, ``weekly`` -> ``w7``, ``daily`` -> ``d1``.
    Rows without a repo id or without the matching gain text are skipped.
    """
    if period not in GAIN_PATTERNS:
        raise ValueError(f"unknown period: {period!r}")
    gain_re = GAIN_PATTERNS[period]
    field = GAIN_FIELD[period]

    records: list[dict] = []
    for block in ARTICLE_RE.findall(page):
        repo = _repo_id(block)
        if not repo:
            continue
        gain = gain_re.search(block)
        if not gain:
            continue

        stars = 0
        link = STARS_LINK_RE.search(block)
        if link:
            numbers = NUMBER_RE.findall(link.group(1))
            if numbers:
                stars = _to_int(numbers[-1])

        desc_match = DESC_RE.search(block)
        lang_match = LANG_RE.search(block)
        records.append(
            {
                "repo": repo,
                "stars": stars,
                "m30": _to_int(gain.group(1)) if field == "m30" else None,
                "w7": _to_int(gain.group(1)) if field == "w7" else None,
                "d1": _to_int(gain.group(1)) if field == "d1" else None,
                "desc": html_lib.unescape(_clean(desc_match.group(1))) if desc_match else "",
                "lang": lang_match.group(1) if lang_match else "",
            }
        )
    return records
