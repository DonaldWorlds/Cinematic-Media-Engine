"""ffprobe-based media metadata extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..utils.ffmpeg import ffprobe_json


class MediaMetadata(BaseModel):
    """Structured probe of a source media file."""

    path: str
    duration_sec: float
    width: int
    height: int
    fps: float
    video_codec: str | None
    audio_codec: str | None
    has_video: bool
    has_audio: bool
    size_bytes: int


def _parse_fps(rate: str) -> float:
    if "/" in rate:
        num, den = rate.split("/", 1)
        denominator = float(den)
        return float(num) / denominator if denominator else 0.0
    return float(rate)


def extract_metadata(path: Path, *, ffprobe_binary: str = "ffprobe") -> MediaMetadata:
    """Probe ``path`` with ffprobe and return structured metadata."""
    probe = ffprobe_json(path, binary=ffprobe_binary)
    streams: list[dict[str, Any]] = probe.get("streams", [])
    fmt: dict[str, Any] = probe.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration", 0.0) or 0.0)
    if duration <= 0.0 and video is not None:
        duration = float(video.get("duration", 0.0) or 0.0)

    size_bytes = int(fmt.get("size", 0) or 0)
    if size_bytes == 0 and path.exists():
        size_bytes = path.stat().st_size

    return MediaMetadata(
        path=str(path),
        duration_sec=duration,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=_parse_fps(str(video.get("avg_frame_rate", "0/1"))) if video else 0.0,
        video_codec=str(video["codec_name"]) if video and "codec_name" in video else None,
        audio_codec=str(audio["codec_name"]) if audio and "codec_name" in audio else None,
        has_video=video is not None,
        has_audio=audio is not None,
        size_bytes=size_bytes,
    )
