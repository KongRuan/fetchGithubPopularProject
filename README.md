# fetchGithubPopularProject

Rank GitHub repositories by the number of stars they **recently gained**, and emit a
Markdown report (in Chinese). The default view is "top 20 by stars gained in the last
30 days".

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

## Requirements

- Python 3.11+ (developed and tested on 3.11).
- GitHub Trending pages are scraped directly from `github.com/trending` without a
  token — the GitHub API rate limit does not apply to scraping.
- `github_token` is accepted but currently reserved: the pipeline makes no GitHub
  REST API calls today. The REST limits (60 requests/hour anonymous, 5,000/hour with
  a token) would only come into play if/when the tool starts using the REST API.

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
python run.py render --by-growth   # fastest-rising board, ranked by relative growth
python run.py skills          # AI agent-skill leaderboard (see below)
python run.py --config my.yaml
python run.py -v              # verbose logging
```

The pipeline is date-scoped: pages fetched today are cached under
`data/cache/pages/<today>/`, so re-running the same day reuses them and finishes fast.
A new day fetches fresh data automatically.

## AI Skill 榜单

`python run.py skills` writes `output/rank_skills_<date>.md`: a leaderboard of AI
coding-agent skill repositories (GitHub Search across the `agent-skills`,
`claude-skills`, `codex-skills`, `cursor-skills` and `opencode-skills` topics plus
phrase searches). Each query runs twice — once with `created:>{window start}` — so
the pool holds both this window's releases and established skill collections.

How a gain is measured, in order:

1. **search-new** (`新星`): the repository was created inside the window, so its
   current star total *is* its gain — a real number, no estimate.
2. **snapshot** (`本地追踪`): an older repository measured against the tool's own
   daily star ledger (`data/cache/stars/<date>.json`). A baseline must come from a
   reading at or before the window start; otherwise the gain is unknown and the
   row cannot rank.
3. **trending** (`官方`): a same-day `merge` wins where it covers the repository.

The ledger is written on every run, so established skill repositories join the
board automatically once 30 days of readings exist (or 7 if `period_days: 7`).
Pools and ledger are cached per day under `data/cache/`.

## Tests
## Configuration

```yaml
period_days: 30              # 1, 7 or 30 (GitHub Trending granularity)
top_n: 20                    # rows in the report table
languages: [...]             # Trending language slugs to scan
github_token: ""             # optional: reserved for GitHub REST API use
enable_ossinsight_estimate: true
output_dir: output
```

## Data sources and accuracy

| Source | Provides | Status |
|---|---|---|
| GitHub Trending `stars this month` | official 30-day gain | authoritative |
| GitHub Trending `stars this week` | official 7-day gain | reference column |
| OSSInsight `stargazers/history` | estimated gain when Trending has no figure | fallback, flagged `估算`, ~±5% |

An estimate is discarded when it cannot be computed (fewer than 2 data points), when
it has no total star count to check against, or when it exceeds 110% of the
repository's total stars. Official Trending figures are never discarded.

## Tests

```bash
pytest -v
```

Fully offline: parsing runs against committed HTML fixtures, merging/rendering/config
are pure functions, and all HTTP calls in the tests are stubbed.

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
