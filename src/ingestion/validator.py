"""Input file validation."""

from __future__ import annotations

from pathlib import Path

from ..utils.ffmpeg import FFmpegError
from .metadata import MediaMetadata, extract_metadata


class InputValidationError(Exception):
    """Raised when an input file is missing or not a usable video."""


def validate_input(path: Path, *, ffprobe_binary: str = "ffprobe") -> MediaMetadata:
    """Validate ``path`` is an existing, probeable video with positive duration.

    Returns the extracted metadata so callers don't re-probe.
    """
    if not path.exists():
        raise InputValidationError(f"input file does not exist: {path}")
    if not path.is_file():
        raise InputValidationError(f"input path is not a file: {path}")
    try:
        meta = extract_metadata(path, ffprobe_binary=ffprobe_binary)
    except FFmpegError as exc:
        raise InputValidationError(f"could not probe {path}: {exc}") from exc
    if not meta.has_video:
        raise InputValidationError(f"no video stream found in {path}")
    if meta.duration_sec <= 0.0:
        raise InputValidationError(f"non-positive duration for {path}")
    return meta
