# fetchGithubPopularProject

Rank GitHub repositories by the number of stars they **recently gained**, and emit a
Markdown report (in Chinese). The default view is "top 20 by stars gained in the last
30 days".

## How it works

```
fetch      GitHub Trending: global page + 33 language pages,
           daily / weekly / monthly windows  ->  data/cache/pages/<date>/
     |
merge      parse -> dedupe -> fill missing gains from GitHub star history,
           with OSSInsight as fallback -> drop implausible estimates ->
           revalidate fallback estimates on the report using a daily series
     |
render     output/rank_<date>.md  +  data/results/rank_<date>.json
```

## Requirements

- Python 3.11+ (developed and tested on 3.11).
- GitHub Trending pages are scraped directly from `github.com/trending` without a
  token — the GitHub API rate limit does not apply to scraping.
- Missing window gains use GitHub's REST star-history API. Set `github_token` for
  a larger rate limit (typically 5,000 requests/hour vs. 60 anonymous); without
  a token, rate limiting triggers OSSInsight fallback. Secondary limits may also
  apply. Trending scraping itself does not consume REST API quota.

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

## Configuration

```yaml
period_days: 30              # 1, 7 or 30 (GitHub Trending granularity)
top_n: 20                    # rows in the report table
languages: [...]             # Trending language slugs to scan
github_token: ""             # optional: GitHub REST star-history requests
enable_github_history: true  # first choice for missing Trending gains
enable_ossinsight_estimate: true  # fallback if GitHub history is unavailable
enable_chinese_summary: true # translate ranked descriptions
output_dir: output
```

Chinese descriptions reuse the same-day cache. To use a cloud translation API,
set `AZURE_TRANSLATOR_KEY` (and `AZURE_TRANSLATOR_REGION` for regional or
multi-service resources) for **Azure Translator**, or `GOOGLE_TRANSLATE_API_KEY`
for **Google Cloud Translation Basic** in the process environment. The order is
Azure → Google → **MyMemory (anonymous, no registration or API key)** → existing
local translator (`127.0.0.1:3425`). Without cloud credentials MyMemory is
tried first, so the report can be translated even when the local model is down.
MyMemory allows [5,000 characters/day anonymously](https://mymemory.translated.net/doc/usagelimits.php)
and at most [500 UTF-8 bytes per request](https://mymemory.translated.net/doc/spec.php);
longer descriptions skip it rather than being truncated. Any provider that
fails twice is skipped for the rest of that render. Translation failures leave
the English original in the report; no credentials are written into config or
cached files. Public descriptions sent to MyMemory are transmitted to a third
party; disable `enable_chinese_summary` to avoid translation requests.

Azure's [F0 free tier](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/translator/)
allows 2 million characters/month. Google's [Basic pricing](https://cloud.google.com/translate/pricing)
includes 500,000 characters/month at no charge, **but requires billing enabled
and charges beyond the free allowance**. Select the F0 Azure resource or set
Google quotas/budget alerts if you need to avoid unexpected charges; these APIs
are not unlimited free services. See the [Azure REST authentication guide](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/authentication)
and [Google Basic authentication guide](https://docs.cloud.google.com/translate/docs/authentication)
for setup.

## Data sources and accuracy

| Source | Provides | Status |
|---|---|---|
| GitHub Trending `stars this month` | official 30-day gain | authoritative |
| GitHub Trending `stars this week` | official 7-day gain | reference column |
| GitHub REST `stargazers/history` | daily new stars for repos without a Trending gain | first historical source, flagged `GitHub 历史` |
| OSSInsight `stargazers/history` | estimated gain when GitHub history is unavailable/incomplete | fallback, flagged `估算`, ~±5% |

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
