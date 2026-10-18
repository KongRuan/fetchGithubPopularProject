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
    # Repo descriptions are arbitrary user text: an unescaped `|` would split
    # the row, and a stray CR/LF would break the table line.
    return (text or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


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
