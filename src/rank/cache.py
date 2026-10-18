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
