"""Command line entry point: fetch -> merge -> render, or any single step.

Exit codes: 0 success, 1 a step had nothing to work with (no pages, no merged
file, nothing ranked), 2 a bad config. With no subcommand all three steps run
and the first non-zero code stops the pipeline.

Everything is date-scoped. ``day`` is today as ``YYYY-MM-DD``. Fetch writes
HTML under ``pages/<day>/``; merge reads that day and writes ``merged.json``
tagged with ``merged_on``. Render labels the report from ``merged_on``, not
from "today", so a report built off yesterday's merge says yesterday.
"""
from __future__ import annotations

import argparse
import functools
import json
import logging
import sys
from datetime import date, timedelta

import requests

from . import estimate as est
from .cache import Cache
from .config import ConfigError, Settings, load_config
from .fetch import WINDOWS as FETCH_WINDOWS
from .fetch import TrendingFetcher
from .merge import apply_sanity_rules, merge_records, rank
from .parse import parse_trending_html
from .report import render_markdown

log = logging.getLogger(__name__)

# Written by step_merge, read by step_render.
# {"merged_on": "YYYY-MM-DD", "records": {"owner/name": record, ...}}
MERGED_KEY = "merged.json"


def _window(settings: Settings, today: date) -> tuple[date, date]:
    """Inclusive ``[today - period_days, today]`` passed to OSSInsight."""
    return today - timedelta(days=settings.period_days), today


def _load_pages(settings: Settings, cache: Cache, day: str) -> list[list[dict]]:
    """Parse every cached Trending page for ``day``.

    A missing file is skipped, not an error: fetch logs and continues when a
    single language slug 404s, and merge should rank whatever did download.
    The global page (``language is None``) is stored as slug ``all``.
    """
    pages: list[list[dict]] = []
    for window in FETCH_WINDOWS:
        for language in (None, *settings.languages):
            slug = language or "all"
            key = f"pages/{day}/trending_{window}_{slug}.html"
            if not cache.exists(key):
                continue
            # FETCH_WINDOWS values are exactly parse_trending_html's periods.
            pages.append(parse_trending_html(cache.read_text(key), window))
    return pages


def step_fetch(settings: Settings, cache: Cache, day: str) -> int:
    """Download Trending pages into the date-scoped cache. 0 if any page exists."""
    fetcher = TrendingFetcher()
    keys = fetcher.fetch_all(settings, cache, day)
    expected = len(FETCH_WINDOWS) * (1 + len(settings.languages))
    if len(keys) < expected:
        # A single bad language slug must not abort a run with many good
        # pages, but the loss must never be silent.
        log.warning(
            "only %d/%d Trending pages available; the report will be based on partial data",
            len(keys), expected,
        )
    log.info("fetched/reused %d Trending pages", len(keys))
    return 0 if keys else 1


def step_merge(settings: Settings, cache: Cache, day: str, today: date) -> int:
    """Parse, estimate, sanity-check, revalidate the top list, write merged.json.

    Sanity rules run twice. The first pass drops estimates that cannot survive
    ranking, so ``revalidate`` only spends daily-series calls on rows that
    could make the report. The second pass repeats the check on the numbers
    revalidate just wrote — a daily delta can still be implausible.
    """
    pages = _load_pages(settings, cache, day)
    if not pages:
        log.error("no cached pages for %s - run `fetch` first", day)
        return 1
    records = merge_records(pages)
    date_from, date_to = _window(settings, today)
    # One shared session for the whole estimate sweep. Without connection
    # reuse every OSSInsight call pays a fresh TLS handshake, which measured
    # at ~4.9s per request and pushed the first real run past 10 minutes.
    with requests.Session() as session:
        history = functools.partial(est.fetch_history, session=session)
        est.fill_estimates(
            records,
            period_days=settings.period_days,
            enable=settings.enable_ossinsight_estimate,
            date_from=date_from,
            date_to=date_to,
            history=history,
            cache=cache,
            day=day,
        )
        apply_sanity_rules(records)
        top, _ = rank(records, settings.top_n)
        est.revalidate(top, date_from=date_from, date_to=date_to,
                       history=history, cache=cache, day=day)
    apply_sanity_rules(records)
    # The wrapper (not bare records) lets a later render label the report with
    # the day the data was actually merged instead of "today".
    cache.write_json(MERGED_KEY, {"merged_on": day, "records": records})
    log.info("merged %d repositories (%d ranked)", len(records),
             sum(1 for r in records.values() if r.get("m30") is not None))
    return 0


def step_render(settings: Settings, cache: Cache, today: date) -> int:
    """Write ``output/rank_<merged_on>.md`` and ``data/results/rank_<merged_on>.json``.

    Prints the report path on stdout (the only thing a caller should parse);
    everything else goes through logging.
    """
    payload = cache.read_json(MERGED_KEY)
    if not isinstance(payload, dict) or "records" not in payload:
        log.error("no merged data - run `merge` first")
        return 1
    records = payload["records"]
    try:
        run_date = date.fromisoformat(payload["merged_on"])
    except (KeyError, TypeError, ValueError) as exc:
        log.error("merged data has no usable date (%s) - run `merge` again", exc)
        return 1
    if run_date != today:
        log.info("report reflects data merged on %s", run_date.isoformat())

    top, rest = rank(records, settings.top_n)
    if not top:
        log.error("no repositories have a %s-day gain", settings.period_days)
        return 1
    date_from, date_to = _window(settings, run_date)
    report = render_markdown(
        top, rest,
        period_days=settings.period_days,
        top_n=settings.top_n,
        scanned=len(records),
        run_date=run_date,
        window=(date_from.isoformat(), date_to.isoformat()),
    )
    day = run_date.isoformat()
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
    # basicConfig is a no-op once the root logger has a handler, so pin the
    # level explicitly: otherwise the first call's verbosity would stick for
    # any subsequent in-process call to main().
    logging.getLogger().setLevel(logging.DEBUG if args.verbose else logging.INFO)
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
