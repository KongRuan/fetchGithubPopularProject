# GitHub Star Ranking Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that produces a Chinese Markdown report ranking GitHub repositories by stars gained in the last N days (default 30), using GitHub Trending as the authoritative source and OSSInsight as a fallback estimator.

**Architecture:** Pure-function core (`parse`, `merge`, `report`, `config`) with I/O pushed to the edges (`fetch`, `estimate`, `cache`). A date-scoped cache makes the pipeline resumable; a `merge` step fills missing 30-day gains by estimating from OSSInsight star history, validates them, revalidates the ones that make the report, then `render` emits the Markdown.

**Tech Stack:** Python 3.10+, `requests`, `PyYAML`, `pytest`. No web framework, no database.

**Spec:** `docs/superpowers/specs/2026-10-18-github-star-ranking-design.md`

---

## Spec amendment (applies throughout)

The spec's discard rule ("estimate exceeds total star count → discard") would wrongly discard
`morluto/rea` (estimate 305,193 vs total 303,026 — legitimate, caused by star churn on a
brand-new viral repo) while catching `apache/predictionio` (estimate 14,413 vs total 12,515).

**Rule used by this plan:** discard an *estimate* only when
`estimate > total_stars * 1.10`. Official Trending figures are never discarded.
The spec file is updated in Task 10.

## File structure

| File | Responsibility |
|---|---|
| `run.py` | Thin entry point; delegates to `src.rank.cli.main` |
| `src/rank/config.py` | Load + validate `config.yaml` → `Settings` dataclass (pure) |
| `src/rank/parse.py` | Trending HTML string → record dicts (pure) |
| `src/rank/merge.py` | Dedupe, precedence, sanity rules, sort, truncate (pure) |
| `src/rank/report.py` | Records → Chinese Markdown (pure) |
| `src/rank/cache.py` | Date-scoped file cache with JSON helpers |
| `src/rank/fetch.py` | Trending HTTP GET with retry/backoff |
| `src/rank/estimate.py` | OSSInsight history → 30-day delta |
| `src/rank/cli.py` | argparse; default = full pipeline, plus subcommands |
| `tests/fixtures/trending_mini.html` | Hand-written HTML with known values |
| `data/samples/*.html` | Real Trending excerpts (smoke tests) |

---

### Task 1: Project skeleton, Git, dependencies

**Files:**
- Create: `.gitignore`, `requirements.txt`, `conftest.py`, `src/__init__.py`, `src/rank/__init__.py`, `tests/__init__.py`
- Create: `data/samples/trending_monthly.html`, `data/samples/trending_weekly.html`, `data/samples/trending_daily.html` (extracted from the validated cached pages)

- [ ] **Step 1: Initialize repository and folders**

Run:
```bash
cd I:\00project\fetchGithubPopularProject
git init
mkdir -p src/rank tests/fixtures data/samples data/cache data/results output docs/superpowers/plans
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
.venv/
data/cache/
output/
```

- [ ] **Step 3: Write `requirements.txt`**

```text
requests>=2.31
PyYAML>=6.0
pytest>=8.0
```

- [ ] **Step 4: Write `conftest.py`** (makes `src` importable without installing the package)

```python
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
```

- [ ] **Step 5: Create empty package files**

```text
src/__init__.py        -> (empty file)
src/rank/__init__.py   -> (empty file)
tests/__init__.py      -> (empty file)
```

- [ ] **Step 6: Extract real HTML fixtures from the validated cache**

Run (PowerShell):
```powershell
function Extract-Articles($src, $dest, $count) {
  $h = [System.IO.File]::ReadAllText($src, [Text.Encoding]::UTF8)
  $m = [regex]::Matches($h, '<article class="Box-row">.*?</article>', 'Singleline')
  $n = [Math]::Min($count, $m.Count)
  $out = (0..($n-1) | ForEach-Object { $m[$_].Value }) -join "`n"
  [System.IO.File]::WriteAllText($dest, $out, [Text.UTF8Encoding]::new($false))
  "$dest <- $n articles"
}
$T = $env:TEMP
Extract-Articles "$T\gh_monthly.html" "I:\00project\fetchGithubPopularProject\data\samples\trending_monthly.html" 3
Extract-Articles "$T\gh_weekly.html"   "I:\00project\fetchGithubPopularProject\data\samples\trending_weekly.html"   3
Extract-Articles "$T\gh_daily.html"    "I:\00project\fetchGithubPopularProject\data\samples\trending_daily.html"    3
```
Expected: three files created, each containing 3 `<article class="Box-row">` blocks.

If `$env:TEMP\gh_*.html` no longer exist, fetch them first:
```powershell
Invoke-WebRequest -Uri "https://github.com/trending?since=monthly" -UseBasicParsing -OutFile "$env:TEMP\gh_monthly.html" -Headers @{"User-Agent"="Mozilla/5.0"}
Invoke-WebRequest -Uri "https://github.com/trending?since=weekly"   -UseBasicParsing -OutFile "$env:TEMP\gh_weekly.html"   -Headers @{"User-Agent"="Mozilla/5.0"}
Invoke-WebRequest -Uri "https://github.com/trending?since=daily"    -UseBasicParsing -OutFile "$env:TEMP\gh_daily.html"    -Headers @{"User-Agent"="Mozilla/5.0"}
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: project skeleton, deps, real Trending HTML fixtures"
```

---

### Task 2: Config loading

**Files:**
- Create: `config.yaml`
- Create: `src/rank/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import pytest

from src.rank.config import ConfigError, DEFAULT_LANGUAGES, Settings, load_config


def test_defaults_from_written_config(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "period_days: 30\n"
        "top_n: 20\n"
        "languages: [python, rust]\n"
        'github_token: ""\n'
        "enable_ossinsight_estimate: true\n"
        "output_dir: output\n",
        encoding="utf-8",
    )
    s = load_config(tmp_path / "config.yaml")
    assert s.period_days == 30
    assert s.top_n == 20
    assert s.languages == ("python", "rust")
    assert s.github_token == ""
    assert s.enable_ossinsight_estimate is True
    assert s.trending_period == "monthly"


def test_rejects_unknown_key(tmp_path):
    (tmp_path / "config.yaml").write_text("top_n: 5\nbogus_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config.yaml")
    assert "bogus_key" in str(e.value)


def test_rejects_unsupported_period(tmp_path):
    (tmp_path / "config.yaml").write_text("period_days: 45\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config.yaml")
    assert "period_days" in str(e.value)


def test_rejects_bad_top_n(tmp_path):
    (tmp_path / "config.yaml").write_text("top_n: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "config.yaml")


def test_settings_exposes_default_languages():
    assert len(DEFAULT_LANGUAGES) == 33
    assert "python" in DEFAULT_LANGUAGES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rank.config'`

- [ ] **Step 3: Write `config.yaml`**

```yaml
# Star ranking window in days. GitHub Trending only publishes daily/weekly/monthly,
# so only 1, 7 and 30 are accepted.
period_days: 30
# How many repositories to put in the report table.
top_n: 20
# Language slugs used for https://github.com/trending/<slug> pages.
languages:
  - python
  - typescript
  - javascript
  - rust
  - go
  - java
  - "c++"
  - "c%23"
  - shell
  - ruby
  - php
  - kotlin
  - swift
  - dart
  - scala
  - haskell
  - elixir
  - lua
  - r
  - zig
  - html
  - css
  - jupyter-notebook
  - clojure
  - powershell
  - solidity
  - objective-c
  - perl
  - "f%23"
  - erlang
  - ocaml
  - fortran
  - vue
# Optional: raises GitHub REST limit from 60 to 5000 requests/hour.
github_token: ""
# Fill in missing window gains from OSSInsight star history.
enable_ossinsight_estimate: true
# Report destination (relative to project root).
output_dir: output
```

- [ ] **Step 4: Write `src/rank/config.py`**

```python
"""Load and validate the project configuration file."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ALLOWED_PERIODS = (1, 7, 30)
PERIOD_NAME = {1: "daily", 7: "weekly", 30: "monthly"}

DEFAULT_LANGUAGES = (
    "python", "typescript", "javascript", "rust", "go", "java", "c++", "c%23",
    "shell", "ruby", "php", "kotlin", "swift", "dart", "scala", "haskell",
    "elixir", "lua", "r", "zig", "html", "css", "jupyter-notebook", "clojure",
    "powershell", "solidity", "objective-c", "perl", "f%23", "erlang", "ocaml",
    "fortran", "vue",
)

_ALLOWED_KEYS = {
    "period_days", "top_n", "languages", "github_token",
    "enable_ossinsight_estimate", "output_dir",
}


class ConfigError(Exception):
    """Raised when the configuration file is missing a key or has a bad value."""


@dataclass(frozen=True)
class Settings:
    root: Path
    period_days: int = 30
    top_n: int = 20
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    github_token: str = ""
    enable_ossinsight_estimate: bool = True
    output_dir: str = "output"

    @property
    def trending_period(self) -> str:
        """GitHub Trending `since=` parameter matching period_days."""
        return PERIOD_NAME[self.period_days]

    @property
    def output_path(self) -> Path:
        return self.root / self.output_dir

    @property
    def cache_dir(self) -> Path:
        return self.root / "data" / "cache"

    @property
    def results_dir(self) -> Path:
        return self.root / "data" / "results"


def load_config(path: str | Path) -> Settings:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")

    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")

    defaults = Settings(root=path.resolve().parent)
    period = int(raw.get("period_days", defaults.period_days))
    if period not in ALLOWED_PERIODS:
        raise ConfigError(
            f"period_days must be one of {ALLOWED_PERIODS} (GitHub Trending "
            f"granularity), got {period}"
        )
    top_n = int(raw.get("top_n", defaults.top_n))
    if top_n < 1:
        raise ConfigError(f"top_n must be >= 1, got {top_n}")

    languages = raw.get("languages", list(DEFAULT_LANGUAGES))
    if not isinstance(languages, list) or not languages:
        raise ConfigError("languages must be a non-empty list")
    languages = tuple(str(x) for x in languages)

    token = raw.get("github_token", "")
    if token is None:
        token = ""
    if not isinstance(token, str):
        raise ConfigError("github_token must be a string")

    estimate = raw.get("enable_ossinsight_estimate", True)
    if not isinstance(estimate, bool):
        raise ConfigError("enable_ossinsight_estimate must be true or false")

    output_dir = str(raw.get("output_dir", defaults.output_dir))
    if not output_dir:
        raise ConfigError("output_dir must not be empty")

    return Settings(
        root=path.resolve().parent,
        period_days=period,
        top_n=top_n,
        languages=languages,
        github_token=token,
        enable_ossinsight_estimate=estimate,
        output_dir=output_dir,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add config.yaml src/rank/config.py tests/test_config.py
git commit -m "feat: config loading with validation"
```

---

### Task 3: HTML parsing

**Files:**
- Create: `tests/fixtures/trending_mini.html`
- Create: `src/rank/parse.py`
- Test: `tests/test_parse.py`

- [ ] **Step 1: Write the hand-crafted fixture with known values**

```html
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/octocat/hello-world">octocat / hello-world</a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A tiny <b>demo</b> &amp; sample</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/octocat/hello-world/stargazers"><svg></svg> 1,234</a>
  <span>1,000 stars this month</span>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/foo/bar">foo / bar</a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Second row, no stars link</p>
  <span itemprop="programmingLanguage">Go</span>
  <span>88 stars this week</span>
</article>
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_parse.py
from pathlib import Path

from src.rank.parse import parse_trending_html

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def test_mini_monthly_parses_known_values():
    html = (FIXTURES / "trending_mini.html").read_text(encoding="utf-8")
    rows = parse_trending_html(html, "monthly")
    assert len(rows) == 1
    row = rows[0]
    assert row["repo"] == "octocat/hello-world"
    assert row["m30"] == 1000
    assert row["stars"] == 1234
    assert row["lang"] == "Python"
    assert row["desc"] == "A tiny demo & sample"
    assert row["w7"] is None


def test_mini_weekly_parses_only_matching_period():
    html = (FIXTURES / "trending_mini.html").read_text(encoding="utf-8")
    rows = parse_trending_html(html, "weekly")
    assert len(rows) == 1
    assert rows[0]["repo"] == "foo/bar"
    assert rows[0]["w7"] == 88
    assert rows[0]["stars"] == 0


def test_unknown_period_raises():
    import pytest

    with pytest.raises(ValueError):
        parse_trending_html("<article class='Box-row'></article>", "fortnight")


def test_real_monthly_sample():
    html = (SAMPLES / "trending_monthly.html").read_text(encoding="utf-8")
    rows = parse_trending_html(html, "monthly")
    assert len(rows) == 3
    for row in rows:
        assert "/" in row["repo"]
        assert row["m30"] > 0
        assert row["stars"] > 0
    gains = [r["m30"] for r in rows]
    assert gains == sorted(gains, reverse=True)  # Trending is pre-sorted


def test_real_weekly_and_daily_samples():
    weekly = parse_trending_html(
        (SAMPLES / "trending_weekly.html").read_text(encoding="utf-8"), "weekly")
    daily = parse_trending_html(
        (SAMPLES / "trending_daily.html").read_text(encoding="utf-8"), "daily")
    assert len(weekly) == 3 and all(r["w7"] > 0 for r in weekly)
    assert len(daily) == 3 and all(r["d1"] > 0 for r in daily)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rank.parse'`

- [ ] **Step 4: Write `src/rank/parse.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_parse.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/rank/parse.py tests/test_parse.py tests/fixtures/trending_mini.html
git commit -m "feat: parse GitHub Trending HTML into records"
```

---

### Task 4: Cache

**Files:**
- Create: `src/rank/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cache.py
from src.rank.cache import Cache


def test_write_and_read_text(tmp_path):
    cache = Cache(tmp_path)
    cache.write_text("pages/a.html", "<html>héllo</html>")
    assert cache.exists("pages/a.html")
    assert cache.read_text("pages/a.html") == "<html>héllo</html>"


def test_json_roundtrip(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("est/x.json", {"gain": 12})
    assert cache.read_json("est/x.json") == {"gain": 12}


def test_missing_json_returns_none(tmp_path):
    assert Cache(tmp_path).read_json("nope.json") is None


def test_corrupt_json_returns_none(tmp_path):
    cache = Cache(tmp_path)
    cache.write_text("bad.json", "{not json")
    assert cache.read_json("bad.json") is None


def test_key_can_contain_directories(tmp_path):
    cache = Cache(tmp_path)
    cache.write_text("pages/deep/nested.html", "x")
    assert (tmp_path / "pages" / "deep" / "nested.html").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rank.cache'`

- [ ] **Step 3: Write `src/rank/cache.py`**

```python
"""Date-scoped file cache, used to make network steps resumable."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path(self, key: str) -> Path:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def read_text(self, key: str) -> str:
        return self.path(key).read_text(encoding="utf-8")

    def write_text(self, key: str, text: str) -> None:
        self.path(key).write_text(text, encoding="utf-8")

    def read_json(self, key: str) -> Any | None:
        try:
            return json.loads(self.read_text(key))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def write_json(self, key: str, value: Any) -> None:
        self.write_text(key, json.dumps(value, ensure_ascii=False, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/rank/cache.py tests/test_cache.py
git commit -m "feat: resumable file cache"
```

---

### Task 5: Merge, validation and ranking

This is where the ranking rules from the spec live.

**Files:**
- Create: `src/rank/merge.py`
- Test: `tests/test_merge.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_merge.py
from src.rank.merge import (
    ESTIMATE_MARGIN,
    apply_sanity_rules,
    merge_records,
    rank,
)


def _row(repo, stars=0, m30=None, w7=None, d1=None, desc="", lang=""):
    return {"repo": repo, "stars": stars, "m30": m30, "w7": w7,
            "d1": d1, "desc": desc, "lang": lang}


def test_merge_dedupes_and_takes_max_gain():
    pages = [
        [_row("a/b", stars=100, m30=50, desc="first", lang="Go")],
        [_row("a/b", stars=100, m30=60, desc="", lang="")],
    ]
    recs = merge_records(pages)
    assert recs["a/b"]["m30"] == 60          # same window, keep the larger figure
    assert recs["a/b"]["desc"] == "first"    # first non-empty wins
    assert recs["a/b"]["lang"] == "Go"


def test_merge_keeps_fields_from_different_periods():
    pages = [
        [_row("a/b", stars=10, m30=100)],
        [_row("a/b", stars=999, w7=7)],
    ]
    recs = merge_records(pages)
    assert recs["a/b"]["m30"] == 100
    assert recs["a/b"]["w7"] == 7
    assert recs["a/b"]["stars"] == 999


def test_sanity_drops_only_outlier_estimates():
    recs = {
        # estimate 7% over total -> tolerated churn, kept
        "keep": _row("keep", stars=1000, m30=1070),
        # estimate 20% over total -> dirty data, dropped
        "drop": _row("drop", stars=1000, m30=1200),
        # official Trending figure over total -> never dropped
        "official": _row("official", stars=1000, m30=5000),
    }
    recs["keep"]["source"] = "est-week"
    recs["drop"]["source"] = "est-week"
    recs["official"]["source"] = "trending"
    apply_sanity_rules(recs)
    assert recs["keep"]["m30"] == 1070
    assert recs["drop"]["m30"] is None
    assert recs["drop"]["source"] == "dropped"
    assert recs["official"]["m30"] == 5000


def test_rank_sorts_descending_and_truncates():
    recs = {
        "low": _row("low", m30=5),
        "high": _row("high", m30=500),
        "mid": _row("mid", m30=50),
        "none": _row("none", m30=None),
    }
    top, rest = rank(recs, top_n=2)
    assert [r["repo"] for r in top] == ["high", "mid"]
    assert [r["repo"] for r in rest] == ["low"]


def test_rank_breaks_ties_by_repo_name():
    recs = {"z/b": _row("z/b", m30=10), "a/b": _row("a/b", m30=10)}
    top, _ = rank(recs, top_n=2)
    assert [r["repo"] for r in top] == ["a/b", "z/b"]


def test_margin_constant_is_documented():
    assert ESTIMATE_MARGIN == 1.10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merge.py -v`
Expected: FAIL with `ImportError: cannot import name 'merge_records'`

- [ ] **Step 3: Write `src/rank/merge.py`**

```python
"""Merge parsed Trending pages, apply validation rules, and rank."""
from __future__ import annotations

from typing import Iterable

FIELDS = ("repo", "stars", "m30", "w7", "d1", "desc", "lang", "source")

#: Estimates may exceed the current total only by star churn; anything larger
#: is dirty data (see spec amendment: predictionio vs rea).
ESTIMATE_MARGIN = 1.10

#: Source labels recorded on every record.
SOURCE_TRENDING = "trending"
SOURCE_EST_WEEK = "est-week"
SOURCE_EST_DAY = "est-day"
SOURCE_EST_FAILED = "est-failed"
SOURCE_DROPPED = "dropped"

_GAIN_FIELDS = ("m30", "w7", "d1")


def _blank(repo: str) -> dict:
    return {
        "repo": repo,
        "stars": 0,
        "m30": None,
        "w7": None,
        "d1": None,
        "desc": "",
        "lang": "",
        "source": SOURCE_TRENDING,
    }


def merge_records(pages: Iterable[list[dict]]) -> dict[str, dict]:
    """Fold many page-parse results into one record per repository.

    Gain fields keep the largest value seen (the same window reported by two
    pages should agree; taking max makes the result independent of fetch order).
    ``stars``/``desc``/``lang`` fill with the first non-empty value.
    """
    records: dict[str, dict] = {}
    for page in pages:
        for row in page:
            rec = records.setdefault(row["repo"], _blank(row["repo"]))
            for field in _GAIN_FIELDS:
                value = row.get(field)
                if value is None:
                    continue
                current = rec[field]
                rec[field] = value if current is None else max(current, value)
            if row.get("stars"):
                rec["stars"] = max(rec["stars"], int(row["stars"]))
            if not rec["desc"] and row.get("desc"):
                rec["desc"] = row["desc"]
            if not rec["lang"] and row.get("lang"):
                rec["lang"] = row["lang"]
    return records


def apply_sanity_rules(records: dict[str, dict]) -> None:
    """Discard estimates that cannot be reconciled with the total star count.

    Official Trending figures are data of record and are never discarded.
    Mutates records in place.
    """
    for rec in records.values():
        gain = rec.get("m30")
        total = rec.get("stars") or 0
        if gain is None or not total:
            continue
        is_estimate = str(rec.get("source", "")).startswith("est-")
        if is_estimate and gain > total * ESTIMATE_MARGIN:
            rec["m30"] = None
            rec["source"] = SOURCE_DROPPED


def rank(records: dict[str, dict], top_n: int) -> tuple[list[dict], list[dict]]:
    """Return ``(top, rest)`` sorted by 30-day gain descending.

    Repositories without a 30-day figure are excluded from both lists.
    Ties break on repo name so output is deterministic.
    """
    eligible = [r for r in records.values() if r.get("m30") is not None]
    eligible.sort(key=lambda r: (-r["m30"], r["repo"]))
    return eligible[:top_n], eligible[top_n:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merge.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/rank/merge.py tests/test_merge.py
git commit -m "feat: merge, sanity rules and deterministic ranking"
```

---

### Task 6: Report rendering

**Files:**
- Create: `src/rank/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report.py
from datetime import date

from src.rank.report import render_markdown


def _rec(repo, m30, stars, w7=None, desc="", lang="", source="trending"):
    return {"repo": repo, "stars": stars, "m30": m30, "w7": w7,
            "d1": None, "desc": desc, "lang": lang, "source": source}


def test_report_contains_header_table_and_citations():
    top = [
        _rec("octo/one", 5000, 9000, w7=700, desc="第一个", lang="Python"),
        _rec("octo/two", 4000, 12000, w7=None, desc="", lang="Go",
             source="est-week"),
    ]
    rest = [_rec("octo/three", 3000, 4000)]
    md = render_markdown(top, rest, period_days=30, top_n=2,
                         scanned=123, run_date=date(2026, 10, 18),
                         window=("2026-09-18", "2026-10-18"))

    assert "# GitHub 新增 Star 榜单（近 30 天）" in md
    assert "| 1 | **octo/one** | +5,000 | 9,000 | +700 | Python | 第一个 |" in md
    assert "| 2 | **octo/two** | +4,000 | 12,000 | — | Go |  |" in md
    assert "估算" in md                       # source flag rendered
    assert "共扫描 123 个仓库" in md
    assert "2026-09-18 → 2026-10-18" in md
    assert "octo/three" in md                 # runners-up section


def test_report_escapes_pipe_in_description():
    top = [_rec("a/b", 10, 20, desc="a | b")]
    md = render_markdown(top, [], 30, 1, 1, date(2026, 10, 18),
                         ("2026-09-18", "2026-10-18"))
    assert "a \\| b" in md


def test_report_shows_dash_when_weekly_missing():
    md = render_markdown([_rec("a/b", 10, 20, w7=None)], [], 30, 1, 1,
                         date(2026, 10, 18), ("2026-09-18", "2026-10-18"))
    assert "| — |" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rank.report'`

- [ ] **Step 3: Write `src/rank/report.py`**

```python
"""Render the ranking as a Chinese Markdown report."""
from __future__ import annotations

from datetime import date

SOURCE_LABEL = {
    "trending": "官方",
    "est-week": "估算",
    "est-day": "估算",
    "est-failed": "估算失败",
    "dropped": "已剔除",
}

RUNNERS_UP = 5


def _num(value) -> str:
    return "—" if value is None else f"{value:,}"


def _gain(value) -> str:
    return "—" if value is None else f"+{value:,}"


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(
    top: list[dict],
    rest: list[dict],
    *,
    period_days: int,
    top_n: int,
    scanned: int,
    run_date: date,
    window: tuple[str, str],
) -> str:
    date_from, date_to = window
    lines: list[str] = [
        f"# GitHub 新增 Star 榜单（近 {period_days} 天）",
        "",
        f"- 报告日期：{run_date.isoformat()}",
        f"- 统计窗口：{date_from} → {date_to}",
        f"- 共扫描 {scanned} 个仓库，入榜 {len(top)} 个（top_n = {top_n}）",
        "",
        "## 榜单",
        "",
        "| # | 项目 | "
        f"{period_days}天新增 | 总星标 | 7天新增 | 语言 | 说明 | 来源 |",
        "|---:|---|---:|---:|---:|---|---|---|",
    ]
    for index, rec in enumerate(top, start=1):
        repo = rec["repo"]
        lines.append(
            f"| {index} | **[{repo}](https://github.com/{repo})** "
            f"| {_gain(rec['m30'])} "
            f"| {_num(rec['stars'])} "
            f"| {_gain(rec['w7'])} "
            f"| {_cell(rec['lang'])} "
            f"| {_cell(rec['desc'])} "
            f"| {SOURCE_LABEL.get(rec['source'], rec['source'])} |"
        )

    if rest:
        lines += ["", "## 紧随其后", ""]
        for rec in rest[:RUNNERS_UP]:
            lines.append(
                f"- `{rec['repo']}` — {_gain(rec['m30'])}（总 {_num(rec['stars'])}）"
            )

    lines += [
        "",
        "## 数据来源与口径",
        "",
        "- **官方**：GitHub Trending 页面公布的 `stars this month`，",
        "  取全球榜与各语言榜的并集，为权威值。",
        "- **估算**：Trending 未覆盖的仓库，用 OSSInsight 星标历史按天差值补算，",
        "  误差约 ±5%；仅当估算值超过总星标的 10% 时判定为脏数据并剔除。",
        "- 排序键为 30 天新增 star 数，7 天新增仅作参考列。",
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/rank/report.py tests/test_report.py
git commit -m "feat: Chinese Markdown report renderer"
```

---

### Task 7: Fetching Trending pages

**Files:**
- Create: `src/rank/fetch.py`
- Test: `tests/test_fetch.py` (offline: retry logic with a stubbed session)

- [ ] **Step 1: Write the failing tests**

```python
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
        def get(self, url, timeout):
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
        def get(self, url, timeout):
            raise OSError("boom")

    fetcher = TrendingFetcher(session=AlwaysDown(), sleep=lambda _s: None)
    with pytest.raises(FetchError):
        fetcher.get("http://x", retries=3)


def test_builds_expected_urls(tmp_path):
    settings = Settings(root=tmp_path, languages=("python", "c%23"))
    urls = list(TrendingFetcher(session=None, sleep=lambda _s: None)
                .trending_urls(settings))
    assert urls == [
        ("monthly", None, "https://github.com/trending?since=monthly"),
        ("monthly", "python", "https://github.com/trending/python?since=monthly"),
        ("monthly", "c%23", "https://github.com/trending/c%23?since=monthly"),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rank.fetch'`

- [ ] **Step 3: Write `src/rank/fetch.py`**

```python
"""Download GitHub Trending pages with retry and date-scoped caching."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Callable

import requests

from .config import Settings

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
WINDOWS = ("daily", "weekly", "monthly")
BASE = "https://github.com/trending"
DELAY_SECONDS = 0.35


class FetchError(Exception):
    """Raised when a page cannot be downloaded after all retries."""


def page_key(window: str, language: str | None, day: str) -> str:
    slug = language or "all"
    return f"pages/{day}/trending_{window}_{slug}.html"


class TrendingFetcher:
    def __init__(
        self,
        session=None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ) -> None:
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep
        self.retries = retries

    def get(self, url: str, retries: int | None = None) -> str:
        attempts = retries if retries is not None else self.retries
        delay = 1.0
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=30,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                response.encoding = "utf-8"
                return response.text
            except Exception as exc:  # noqa: BLE001 - retry on any transport error
                last = exc
                log.warning("attempt %d/%d failed for %s: %s", attempt, attempts, url, exc)
                if attempt < attempts:
                    self.sleep(delay)
                    delay *= 2
        raise FetchError(f"GET {url} failed after {attempts} attempts: {last}")

    def trending_urls(self, settings: Settings) -> Iterator[tuple[str, str | None, str]]:
        """Yield ``(window, language, url)`` for the global page then each language."""
        for window in WINDOWS:
            yield window, None, f"{BASE}?since={window}"
            for language in settings.languages:
                yield window, language, f"{BASE}/{language}?since={window}"

    def fetch_all(self, settings: Settings, cache, day: str) -> list[str]:
        """Fetch (or reuse cached) pages; returns the cache keys written/read."""
        keys: list[str] = []
        for window, language, url in self.trending_urls(settings):
            key = page_key(window, language, day)
            if cache.exists(key):
                keys.append(key)
                continue
            try:
                cache.write_text(key, self.get(url))
                keys.append(key)
            except FetchError as exc:
                log.error("skipping %s: %s", url, exc)
            self.sleep(DELAY_SECONDS)
        return keys
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/rank/fetch.py tests/test_fetch.py
git commit -m "feat: Trending fetcher with retry and date-scoped cache"
```

---

### Task 8: OSSInsight estimation

**Files:**
- Create: `src/rank/estimate.py`
- Test: `tests/test_estimate.py` (offline: delta math + orchestration with stub fetcher)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_estimate.py
from datetime import date

from src.rank import estimate as est
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_estimate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rank.estimate'`

- [ ] **Step 3: Write `src/rank/estimate.py`**

```python
"""Estimate missing window gains from OSSInsight star history."""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Callable

import requests

from .merge import SOURCE_EST_DAY, SOURCE_EST_FAILED, SOURCE_EST_WEEK

log = logging.getLogger(__name__)

API = "https://api.ossinsight.io/v1/repos/{owner}/{name}/stargazers/history/"
DELAY_SECONDS = 0.25


def history_url(owner: str, name: str, period: str,
                date_from: date, date_to: date) -> str:
    return (
        f"{API.format(owner=owner, name=name)}"
        f"?per={period}&from={date_from.isoformat()}&to={date_to.isoformat()}"
    )


def history_delta(rows: list[dict]) -> int | None:
    """Stars gained across the window, or None when the series is too short."""
    if not rows or len(rows) < 2:
        return None
    first = Decimal(str(rows[0]["stargazers"]).replace(",", ""))
    last = Decimal(str(rows[-1]["stargazers"]).replace(",", ""))
    return int(last - first)


def fetch_history(owner: str, name: str, period: str,
                  date_from: date, date_to: date, cache, day: str) -> list[dict]:
    """Return star-history rows, cached per repo/period/window/day."""
    key = f"est/{day}/{owner}@{name}_{period}_{date_from}_{date_to}.json"
    cached = cache.read_json(key) if cache is not None else None
    if cached is not None:
        return cached

    response = requests.get(
        history_url(owner, name, period, date_from, date_to),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    rows = (response.json().get("data") or {}).get("rows") or []
    if cache is not None:
        cache.write_json(key, rows)
    return rows


def _owner_name(repo: str) -> tuple[str, str] | None:
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return owner, name


def fill_estimates(records: dict, *, period_days: int, enable: bool,
                   date_from: date, date_to: date,
                   history: Callable, cache, day: str) -> None:
    """Fill ``m30`` for records that have no window gain.

    Mutates records in place. Failures are recorded as ``est-failed`` and
    never abort the run.
    """
    if not enable:
        return
    period = "week" if period_days > 7 else "day"
    for rec in records.values():
        if rec.get("m30") is not None:
            continue
        pair = _owner_name(rec["repo"])
        if pair is None:
            rec["source"] = SOURCE_EST_FAILED
            continue
        owner, name = pair
        try:
            rows = history(owner, name, period, date_from, date_to, cache, day)
        except Exception as exc:  # noqa: BLE001 - one repo must not kill the run
            log.warning("estimate failed for %s: %s", rec["repo"], exc)
            rec["source"] = SOURCE_EST_FAILED
            continue
        delta = history_delta(rows)
        if delta is None:
            rec["source"] = SOURCE_EST_FAILED
            continue
        rec["m30"] = delta
        rec["source"] = SOURCE_EST_WEEK


def revalidate(records: list[dict], *, date_from: date, date_to: date,
               history: Callable, cache, day: str) -> None:
    """Re-fetch estimates that made the report using the denser daily series.

    ``records`` is the ranked top list; entries are updated in place.
    """
    for rec in records:
        if not str(rec.get("source", "")).startswith("est-"):
            continue
        pair = _owner_name(rec["repo"])
        if pair is None:
            continue
        owner, name = pair
        try:
            rows = history(owner, name, "day", date_from, date_to, cache, day)
        except Exception as exc:  # noqa: BLE001
            log.warning("revalidate failed for %s: %s", rec["repo"], exc)
            continue
        delta = history_delta(rows)
        if delta is not None:
            rec["m30"] = delta
            rec["source"] = SOURCE_EST_DAY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_estimate.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/rank/estimate.py tests/test_estimate.py
git commit -m "feat: OSSInsight star-history estimation and revalidation"
```

---

### Task 9: CLI wiring the pipeline together

**Files:**
- Create: `src/rank/cli.py`, `run.py`
- Test: `tests/test_cli.py` (offline: run steps against fixture pages)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json
from datetime import date
from pathlib import Path

from src.rank import cli

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def _write_config(root: Path) -> Path:
    cfg = root / "config.yaml"
    cfg.write_text(
        "period_days: 30\n"
        "top_n: 2\n"
        "languages: [python]\n"
        'github_token: ""\n'
        "enable_ossinsight_estimate: false\n"
        "output_dir: output\n",
        encoding="utf-8",
    )
    return cfg


def test_render_step_from_cached_pages(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    cache_dir = tmp_path / "data" / "cache" / "pages" / "2026-10-18"
    cache_dir.mkdir(parents=True)
    # two different pages holding the same repos, disjoint gain fields
    (cache_dir / "trending_monthly_all.html").write_text(
        (SAMPLES / "trending_monthly.html").read_text(encoding="utf-8"),
        encoding="utf-8")
    (cache_dir / "trending_weekly_all.html").write_text(
        (SAMPLES / "trending_weekly.html").read_text(encoding="utf-8"),
        encoding="utf-8")

    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 18))
    assert cli.main(["--config", str(cfg), "merge"]) == 0
    assert cli.main(["--config", str(cfg), "render"]) == 0

    out = tmp_path / "output" / "rank_2026-10-18.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "# GitHub 新增 Star 榜单（近 30 天）" in text
    assert "https://github.com/" in text

    archived = json.loads(
        (tmp_path / "data" / "results" / "rank_2026-10-18.json")
        .read_text(encoding="utf-8"))
    assert archived["run_date"] == "2026-10-18"
    assert len(archived["top"]) == 2


def test_missing_cache_reports_error(tmp_path):
    cfg = _write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "render"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rank.cli'`

- [ ] **Step 3: Write `src/rank/cli.py`**

```python
"""Command line entry point: fetch -> merge -> render, or any single step."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from . import estimate as est
from .cache import Cache
from .config import ConfigError, Settings, load_config
from .fetch import TrendingFetcher
from .merge import apply_sanity_rules, merge_records, rank
from .parse import parse_trending_html
from .report import render_markdown

log = logging.getLogger(__name__)

MERGED_KEY = "merged.json"


def _window(settings: Settings, today: date) -> tuple[date, date]:
    return today - timedelta(days=settings.period_days), today


def _load_pages(settings: Settings, cache: Cache, day: str) -> list[list[dict]]:
    pages: list[list[dict]] = []
    for window in ("daily", "weekly", "monthly"):
        for language in (None, *settings.languages):
            slug = language or "all"
            key = f"pages/{day}/trending_{window}_{slug}.html"
            if not cache.exists(key):
                continue
            period = {"monthly": "monthly", "weekly": "weekly", "daily": "daily"}[window]
            pages.append(parse_trending_html(cache.read_text(key), period))
    return pages


def step_fetch(settings: Settings, cache: Cache, day: str) -> int:
    fetcher = TrendingFetcher()
    keys = fetcher.fetch_all(settings, cache, day)
    log.info("fetched/reused %d Trending pages", len(keys))
    return 0 if keys else 1


def step_merge(settings: Settings, cache: Cache, day: str, today: date) -> int:
    pages = _load_pages(settings, cache, day)
    if not pages:
        log.error("no cached pages for %s - run `fetch` first", day)
        return 1
    records = merge_records(pages)
    date_from, date_to = _window(settings, today)
    est.fill_estimates(
        records,
        period_days=settings.period_days,
        enable=settings.enable_ossinsight_estimate,
        date_from=date_from,
        date_to=date_to,
        history=est.fetch_history,
        cache=cache,
        day=day,
    )
    apply_sanity_rules(records)
    top, _ = rank(records, settings.top_n)
    est.revalidate(top, date_from=date_from, date_to=date_to,
                   history=est.fetch_history, cache=cache, day=day)
    apply_sanity_rules(records)
    cache.write_json(MERGED_KEY, records)
    log.info("merged %d repositories (%d ranked)", len(records),
             sum(1 for r in records.values() if r.get("m30") is not None))
    return 0


def step_render(settings: Settings, cache: Cache, today: date) -> int:
    records = cache.read_json(MERGED_KEY)
    if not records:
        log.error("no merged data - run `merge` first")
        return 1
    top, rest = rank(records, settings.top_n)
    if not top:
        log.error("no repositories have a %s-day gain", settings.period_days)
        return 1
    date_from, date_to = _window(settings, today)
    report = render_markdown(
        top, rest,
        period_days=settings.period_days,
        top_n=settings.top_n,
        scanned=len(records),
        run_date=today,
        window=(date_from.isoformat(), date_to.isoformat()),
    )
    day = today.isoformat()
    settings.output_path.mkdir(parents=True, exist_ok=True)
    report_path = settings.output_path / f"rank_{day}.md"
    report_path.write_text(report, encoding="utf-8")

    settings.results_dir.mkdir(parents=True, exist_ok=True)
    archive = {
        "run_date": day,
        "period_days": settings.period_days,
        "top": top,
        "runners_up": rest[:5],
        "scanned": len(records),
    }
    (settings.results_dir / f"rank_{day}.json").write_text(
        json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("wrote %s", report_path)
    print(report_path)
    return 0


def _today() -> date:
    """Current date; isolated so tests can freeze it."""
    return date.today()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rank",
        description="Rank GitHub repositories by recently gained stars.",
    )
    parser.add_argument("--config", default="config.yaml",
                        help="path to config file (default: config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("fetch", "download GitHub Trending pages"),
        ("merge", "combine pages, estimate missing gains"),
        ("render", "write the Markdown report"),
    ):
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        settings = load_config(args.config)
    except ConfigError as exc:
        log.error("config error: %s", exc)
        return 2

    today = _today()
    day = today.isoformat()
    cache = Cache(settings.cache_dir)

    steps = {
        "fetch": lambda: step_fetch(settings, cache, day),
        "merge": lambda: step_merge(settings, cache, day, today),
        "render": lambda: step_render(settings, cache, today),
    }
    if args.command:
        return steps[args.command]()

    # default: full pipeline, stop at the first failing step
    for name in ("fetch", "merge", "render"):
        code = steps[name]()
        if code:
            return code
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Write `run.py`**

```python
"""Entry point: python run.py [fetch|merge|render]"""
import sys

from src.rank.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: 2 passed

- [ ] **Step 6: Run the whole suite**

Run: `pytest -v`
Expected: all tests across `test_config`, `test_parse`, `test_cache`, `test_merge`, `test_report`, `test_fetch`, `test_estimate`, `test_cli` pass.

- [ ] **Step 7: Commit**

```bash
git add src/rank/cli.py run.py tests/test_cli.py
git commit -m "feat: CLI with fetch/merge/render steps and full-pipeline default"
```

---

### Task 10: README, spec amendment, archive first results

**Files:**
- Create: `README.md`
- Modify: `docs/superpowers/specs/2026-10-18-github-star-ranking-design.md` (Section 3, rule 2)
- Archive: `data/results/rank_2026-10-18.json` (from the validated prototype output)

- [ ] **Step 1: Amend the spec discard rule**

Replace spec Section 3 rule 2 with:

```markdown
2. An estimate is **discarded** (set to null, source `dropped`) when it cannot be
   computed (fewer than 2 data points) or when it exceeds **110%** of the
   repository's total star count. The 10% tolerance admits legitimate star churn on
   newly viral repositories (e.g. `morluto/rea`, estimate 305,193 vs total 303,026)
   while still rejecting dirty data (`apache/predictionio`, 14,413 vs 12,515).
   Official Trending figures are never discarded.
```

- [ ] **Step 2: Write `README.md`**

```markdown
# fetchGithubPopularProject

Rank GitHub repositories by the number of stars they **recently gained**, and emit a
Markdown report. The default view is "top 20 by stars gained in the last 30 days".

## How it works

```
fetch      GitHub Trending: global page + 33 language pages,
           daily / weekly / monthly windows  ->  data/cache/pages/<date>/
     |
merge      parse -> dedupe -> fill missing 30-day gains from OSSInsight
           star history -> drop implausible estimates -> revalidate the
           entries that make the report using a daily series
     |
render     output/rank_<date>.md  +  data/results/rank_<date>.json
```

## Install

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

## Usage

```bash
python run.py                 # full pipeline (resumes from cache)
python run.py fetch           # download Trending pages only
python run.py merge           # combine pages + estimate missing gains
python run.py render          # write the report
python run.py --config my.yaml
python run.py -v              # verbose logging
```

The pipeline is date-scoped: pages fetched today are cached under
`data/cache/pages/<today>/`, so re-running the same day reuses them and finishes fast.
A new day fetches fresh data automatically.

## Configuration

```yaml
period_days: 30              # 1, 7 or 30 (GitHub Trending granularity)
top_n: 20                    # rows in the report table
languages: [...]             # Trending language slugs to scan
github_token: ""             # optional: 5000 GitHub API requests/hour
enable_ossinsight_estimate: true
output_dir: output
```

## Data sources and accuracy

| Source | Provides | Status |
|---|---|---|
| GitHub Trending `stars this month` | official 30-day gain | authoritative |
| GitHub Trending `stars this week` | official 7-day gain | reference column |
| OSSInsight `stargazers/history` | estimated gain when Trending has no figure | fallback, flagged `估算`, ~±5% |

An estimate is discarded when it exceeds 110% of the repository's total stars.
Official Trending figures are never discarded.

## Tests

```bash
pytest -v
```

Fully offline: parsing runs against committed HTML fixtures, merging/rendering/config
are pure functions.

## Layout

```
run.py                 entry point
config.yaml            tunables
src/rank/              config, fetch, parse, merge, estimate, report, cache, cli
tests/                 pytest suite
data/samples/          real Trending HTML fixtures (committed)
data/cache/            downloads + intermediate state (gitignored)
data/results/          archived reports (committed)
output/                Markdown reports (gitignored)
docs/superpowers/      design spec and plan
```
```

- [ ] **Step 3: Archive the validated prototype result**

Run (PowerShell):
```powershell
$src = Join-Path $env:TEMP "final_rank.json"
$dst = "I:\00project\fetchGithubPopularProject\data\results\raw_prototype_2026-10-18.json"
if (Test-Path $src) {
  Copy-Item $src $dst -Force
  "archived -> $dst"
} else {
  "prototype output not found at $src (skipping)"
}
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-18-github-star-ranking-design.md data/results/
git commit -m "docs: README, spec amendment for 110% estimate margin"
```

---

### Task 11: First real end-to-end run

**Files:**
- Create: `output/rank_<today>.md`, `data/results/rank_<today>.json`

- [ ] **Step 1: Run the full pipeline**

Run: `python run.py -v`
Expected (timing: ~2–4 minutes, dominated by 102 Trending requests + ~180 OSSInsight requests):

```
INFO fetched/reused 102 Trending pages
INFO merged ~679 repositories (~499 ranked)
INFO wrote output\rank_YYYY-MM-DD.md
I:\00project\fetchGithubPopularProject\output\rank_YYYY-MM-DD.md
```
Exit code 0.

- [ ] **Step 2: Verify the report looks right**

Run (PowerShell):
```powershell
Get-ChildItem output\*.md | Select-Object -Last 1 | ForEach-Object {
  Get-Content $_.FullName -TotalCount 25 -Encoding UTF8
}
```

Expected: Chinese title, a Markdown table with `# / 项目 / 30天新增 / 总星标 / 7天新增 / 语言 / 说明 / 来源`
columns, `+`-formatted gains, rows linking to `https://github.com/<owner>/<repo>`,
and a `数据来源与口径` section at the end. Confirm at least one row is flagged `官方`
and, if any were filled in, one flagged `估算`.

- [ ] **Step 3: Re-run to confirm resume works**

Run: `python run.py`
Expected: completes in a few seconds (all pages served from `data/cache/pages/<today>/`),
same report content.

- [ ] **Step 4: Commit**

```bash
git add output/ data/results/ 2>nul || git add data/results/
git commit -m "chore: first end-to-end run report"
```

(Note: `output/` is gitignored by design, so only `data/results/*.json` is expected to
be staged; if `output/` was intentionally un-ignored, both are added.)

- [ ] **Step 5: Final verification**

Run: `pytest -v`
Expected: all tests pass.

---

## Self-review notes

- **Spec coverage:** skeleton/README/git/tests ✔ (Tasks 1, 10); config 6 keys ✔ (Task 2);
  fetch global+33 languages × daily/weekly/monthly ✔ (Task 7); parse ✔ (Task 3);
  merge precedence + 110% margin + per-day revalidation ✔ (Tasks 5, 8);
  CLI one-shot + subcommands ✔ (Task 9); Markdown report with 官方/估算 flags ✔ (Task 6);
  error handling (retry, per-repo failure isolation, UTF-8) ✔ (Tasks 7, 8, 9);
  first real run + archive ✔ (Task 11).
- **Placeholders:** none — every step carries complete code, exact paths and commands.
- **Type consistency:** record shape `{"repo","stars","m30","w7","d1","desc","lang","source"}`
  is identical across `parse` / `merge` / `report` / `estimate` / `cli`; `Settings` field names
  match `config.yaml` keys; `page_key()` signature is used identically in `fetch` and `cli`.
