# Execution Tracker — 2026-10-18-github-star-ranking

Plan: `docs/superpowers/plans/2026-10-18-github-star-ranking.md`
Branch: `star-ranking-pipeline` (created after `git init` in Task 1)

Every task ran implementer → independent spec/quality review → fixes. Test count
grew from 5 (Task 2) to 89; the suite is offline throughout.

| # | Task | Commit | Review outcome |
|---|------|--------|----------------|
| 1 | Project skeleton, Git, dependencies | `a495af4`, `53efc8d` | Compliant |
| 2 | Config loading | `4374fb4` | 1 MAJOR — bare `int()` leaked `ValueError`/`TypeError`; fixed in `64ecfe1` |
| 3 | HTML parsing | `2d17f1e` | 2 MINOR — `&nbsp;` not folded, dead `FIELDS`; fixed in `916a10b` |
| 4 | Cache | `732b0a9` | 1 MAJOR — `read_json` missed `UnicodeDecodeError`, breaking the re-fetch guarantee; `exists()` also created dirs; fixed in `1dba8bf` |
| 5 | Merge, validation, ranking | `0a13082` | Misleading docstring, dead `FIELDS`, `stars == 0` made the sanity rule a no-op; fixed in `dd2d937` |
| 6 | Report rendering | `b6e83c3` | 1 MAJOR — methodology claimed a 10% rule while code enforced 110%; `+-5,000` for negative gains; fixed in `548a674` |
| 7 | Fetching Trending pages | `77fcbb6` | 6 MINOR — broad `except` masked programming errors, 404s retried, causes unchained; fixed in `c091fb0` |
| 8 | OSSInsight estimation | `79cd43e` | 2 MAJOR — one documented-not-a-bug, one an unpinned invariant; fixed in `16fe3dd`; no connection reuse found by Task 11, fixed in `52cb110` |
| 9 | CLI wiring | `e33b733` | 3 MINOR — unused import, `KeyError` on corrupted `merged.json`, inert `-v`; fixed in `4911d3e` |
| 10 | README, spec amendment, archive | `8dddb21` | README claims verified against code; `github_token` wording corrected (it is unused today) |
| 11 | First real end-to-end run | pending | First attempt killed at 600 s — see below |

## Plan defects found and resolved

The plan was self-consistent in code but contradicted itself in three test
texts; each was resolved rather than worked around silently:

- **Task 6** asserted a plain bold repo cell while the implementation and Task 11
  both require a `https://github.com/<owner>/<repo>` link. The assertion was
  wrong; the link is a product requirement.
- **Task 6** called a keyword-only signature with positional arguments.
- **Task 7** asserted 3 URLs where the implementation correctly yields 9, and its
  session stubs omitted the `headers` parameter the implementation sends — which
  the broad `except Exception` would have hidden as a `FetchError`.
- **Task 8** defined `DELAY_SECONDS` without using it and had no retry at all,
  although the design checklist requires one.

## Task 11 — first real run

The run was killed by a 600 s cap. Measured, not guessed:

- 102/102 Trending pages fetched in 164 s, no shortfall.
- Estimate phase completed 87 of **377** repos at **~4.9 s each**.
- Cause: `estimate.fetch_history` used a bare `requests.get`, opening a fresh
  TLS connection per call (89 handshakes), while `fetch.py` reused **one**
  connection for all 102 GitHub pages.
- The workload is 377 not ~180 because the pipeline also merges **daily** pages:
  204 repos appear only there and so lack an official `m30`.

Fixed in `52cb110` by sharing a single `requests.Session` across the sweep.
Daily-only repos are deliberately kept — excluding them would drop genuine hits
whose 30-day figure Trending hasn't published yet.
