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
        # Deliberately bypasses path(): a read must not create directories,
        # and must return False rather than raise when a parent component is
        # already a file (Windows raises FileExistsError from mkdir).
        try:
            return (self.root / key).is_file()
        except OSError:
            return False

    def read_text(self, key: str) -> str:
        return self.path(key).read_text(encoding="utf-8")

    def write_text(self, key: str, text: str) -> None:
        self.path(key).write_text(text, encoding="utf-8")

    def read_json(self, key: str) -> Any | None:
        # UnicodeDecodeError is a ValueError, not an OSError: a write that died
        # mid-character of CJK content leaves valid UTF-8-unclosable bytes, and
        # that must mean "re-fetch", not "crash the run".
        try:
            return json.loads(self.read_text(key))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None

    def write_json(self, key: str, value: Any) -> None:
        self.write_text(key, json.dumps(value, ensure_ascii=False, indent=2))
