"""Thin subprocess wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe subprocess fails or is missing."""


def _run(cmd: list[str], binary: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise FFmpegError(f"{binary} not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "")[-800:]
        raise FFmpegError(f"{binary} failed (rc={exc.returncode}): {tail}") from exc


def run_ffmpeg(args: list[str], *, binary: str = "ffmpeg") -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with ``args`` (binary prefix added). Raises FFmpegError on failure."""
    return _run([binary, *args], binary)


def ffprobe_json(path: Path, *, binary: str = "ffprobe") -> dict[str, Any]:
    """Return ffprobe's JSON (format + streams) for ``path``."""
    args = [
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = _run([binary, *args], binary)
    data: dict[str, Any] = json.loads(result.stdout)
    return data
