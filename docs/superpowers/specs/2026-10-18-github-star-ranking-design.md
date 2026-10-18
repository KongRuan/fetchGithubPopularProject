# Design: fetchGithubPopularProject — GitHub Star Ranking Pipeline

Date: 2026-10-18
Status: Approved

## 1. Purpose

Build a repeatable Python tool that produces a Markdown report ranking GitHub
repositories by **new stars gained over a configurable window (default: 30 days)**,
with a 7-day gain column as secondary reference.

The tool replaces an ad-hoc set of PowerShell scripts that were proven to work
during manual analysis. Ranking logic and data sources are already validated.

## 2. Scope

**In scope**

- Fetch GitHub Trending (global + per-language pages) for daily / weekly / monthly windows.
- Parse HTML into a uniform record shape.
- Merge multiple sources with defined precedence.
- Estimate missing 30-day gains via OSSInsight star history, with a per-day revalidation pass.
- Render one Chinese Markdown report per run into `output/`.
- CLI: one-shot pipeline by default, plus `fetch` / `merge` / `render` subcommands for debugging.
- Config file for the six tunables.
- `pytest` offline tests for parsing, merging, config loading.
- Git repository with an initial commit.

**Out of scope (YAGNI)**

- Scheduled / cron execution.
- Excel or JSON report output (Markdown only).
- Charts, databases, web UI.
- GitHub API crawl of repository metadata beyond what Trending/OSSInsight provide.

## 3. Data sources & ranking rules

| Source | Provides | Precedence |
|---|---|---|
| GitHub Trending `?since=monthly` (global + 33 language pages) | Official 30-day gain | **Authoritative** |
| GitHub Trending `?since=weekly` (global + language pages) | Official 7-day gain | Authoritative |
| GitHub Trending `?since=daily` | Official 1-day gain | Reference only |
| OSSInsight `stargazers/history` | Estimated 30-day gain | Fallback when Trending has no monthly figure |
| GitHub REST `repos/{owner}/{repo}` | total stars, description, language | Metadata enrichment (optional, needs token for volume) |

**Rules**

1. A repository's 30-day gain comes from Trending monthly when available; otherwise it is
   estimated as `history(last) - history(first)` over the window and flagged `估算`.
2. An estimate is **discarded** (set to null) when it cannot be computed (fewer than 2 data
   points) or when it exceeds the repository's total star count — this catches the
   `apache/predictionio` class of dirty data.
3. Estimates that land near/above the ranking cutoff are revalidated with a per-day query
   (`per=day`) rather than `per=week`.
4. Ranked output = repositories with a non-null 30-day gain, sorted descending, truncated to
   `top_n`. Rows below the cut are still kept in the archived JSON for context.
5. Every row records its source: `trending` (官方) / `est-day` / `est-week` (估算) / `dropped`.

## 4. Architecture

```
config.yaml ─┐
             ▼
        config.py ── validates & exposes typed settings
             │
   cli.py ───┼── fetch ──► fetch.py ──► data/cache/pages/*.html
             │                              │
             │                           parse.py   (pure functions, unit-tested)
             │                              │
             ├── merge ──► merge.py ◄───────┘
             │                 │
             │            estimate.py ◄── OSSInsight (cached, resumable)
             │                 │
             │            validation (est>total drop, per-day revalidation)
             │                 │
             └── render ─► report.py ──► output/rank_YYYY-MM-DD.md
                                          data/results/rank_YYYY-MM-DD.json
```

**Unit boundaries** — each module has one job and a narrow interface:

| Module | Responsibility | Depends on | Testable offline |
|---|---|---|---|
| `config.py` | Load/validate `config.yaml` into a typed settings object | pyyaml | yes |
| `parse.py` | HTML string → record dicts | stdlib `re` | yes (fixture HTML) |
| `fetch.py` | HTTP GET with retry/backoff, write cache files | requests, cache | no (network) |
| `merge.py` | Dedupe, precedence, validation, sort, truncate | pure | yes |
| `estimate.py` | OSSInsight history → 30-day delta, resumable cache | requests, cache | partially |
| `report.py` | Records → Chinese Markdown | pure | yes |
| `cache.py` | Keyed read/write with resume semantics | filesystem | yes |
| `cli.py` | argparse wiring, default = full pipeline | all | no |

`fetch`, `estimate` do network I/O and are kept thin; all decision logic lives in
`parse` / `merge` / `report`, which are pure and covered by tests.

## 5. Configuration

```yaml
period_days: 30
top_n: 20
languages: [ ... 33 language slugs ... ]
github_token: ""
enable_ossinsight_estimate: true
output_dir: output
```

Unknown keys or invalid types fail fast with a clear message at startup.

## 6. CLI

```
python run.py              # full pipeline, resumes from cache
python run.py fetch        # step 1 only
python run.py merge        # step 2 only (needs fetch output)
python run.py render       # step 3 only (needs merge output)
python run.py --config path/to/config.yaml
```

## 7. Error handling

- HTTP: 3 retries with exponential backoff; a failed language page is logged and skipped
  (partial coverage beats no report).
- OSSInsight estimate failure for one repo → mark `est_failed`, continue.
- Missing `output/` or `data/` directories are created on demand.
- Encoding: all files read/written as UTF-8 explicitly (Windows default codepage is not UTF-8).

## 8. Testing

`pytest`, fully offline:

- `test_parse.py` — parse real Trending HTML samples committed under `data/samples/`
  (daily/weekly/monthly fixtures); assert repo id, gains, stars extracted correctly.
- `test_merge.py` — precedence (trending beats estimate), discard rule for
  `est > total`, sort order, `top_n` truncation, source flagging.
- `test_config.py` — defaults load, invalid key/type rejected.

## 9. Repository layout

```
fetchGithubPopularProject/
├── README.md                 (English)
├── config.yaml
├── requirements.txt
├── run.py
├── src/rank/{__init__,config,fetch,parse,estimate,merge,report,cache,cli}.py
├── tests/{test_parse,test_merge,test_config}.py
├── data/samples/*.html        (committed fixtures)
├── data/cache/                (gitignored)
├── data/results/              (archived run results, committed)
├── output/                    (gitignored)
├── docs/superpowers/specs/
└── .gitignore
```

## 10. Deliverables checklist

- [ ] Project skeleton + config + requirements
- [ ] `parse`, `merge`, `report`, `cache` pure modules
- [ ] `fetch`, `estimate` network modules with retry + resume cache
- [ ] `cli` with one-shot default and subcommands
- [ ] README (English) with usage
- [ ] Offline pytest suite passing
- [ ] `git init` + initial commit
- [ ] First real run producing `output/rank_2026-10-18.md` and archived JSON in `data/results/`
