"""File I/O helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) as a directory if missing; return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as indented JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> Any:
    """Read and parse JSON from ``path``."""
    return json.loads(path.read_text(encoding="utf-8"))
