"""Load and validate the project configuration file."""
from __future__ import annotations

from dataclasses import dataclass
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


def _as_int(value, key: str) -> int:
    """Coerce an integer config value, reporting failures as ConfigError.

    `bool` is checked first because it is a subclass of `int` in Python, so
    YAML `period_days: true` must not silently mean "daily".
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer, got {value!r}")
    return value


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
    # An absent config and an empty one are equivalent; a falsy non-mapping
    # (YAML `0` or `false`) is not, so branch on None rather than truthiness.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")

    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")

    root = path.resolve().parent
    defaults = Settings(root=root)
    period = _as_int(raw.get("period_days", defaults.period_days), "period_days")
    if period not in ALLOWED_PERIODS:
        raise ConfigError(
            f"period_days must be one of {ALLOWED_PERIODS} (GitHub Trending "
            f"granularity), got {period}"
        )
    top_n = _as_int(raw.get("top_n", defaults.top_n), "top_n")
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

    output_dir = raw.get("output_dir", defaults.output_dir)
    if output_dir is None:
        output_dir = defaults.output_dir
    if not isinstance(output_dir, str) or not output_dir:
        raise ConfigError("output_dir must be a non-empty string")

    return Settings(
        root=root,
        period_days=period,
        top_n=top_n,
        languages=languages,
        github_token=token,
        enable_ossinsight_estimate=estimate,
        output_dir=output_dir,
    )
