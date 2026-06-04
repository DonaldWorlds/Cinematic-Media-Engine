"""Render backend abstraction.

ffmpeg is the first concrete render backend, but the engine talks to it through
this backend-agnostic interface so a future Descript (or other) backend slots in
without touching the orchestrator. Raw ffmpeg calls live ONLY in the concrete
``FFmpegBackend`` (ffmpeg_pipeline.py) — everything else builds a ``ClipRenderSpec``
and hands it to a ``RenderBackend``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RenderFormat(BaseModel):
    """Target output format for a clip (backend-agnostic)."""

    width: int
    height: int
    fps: int
    video_codec: str = "h264"
    audio_codec: str = "aac"
    bitrate: str | None = None


class CaptionLine(BaseModel):
    """One burned caption line with CLIP-RELATIVE timing (0 = clip start)."""

    start_sec: float
    end_sec: float
    text: str


class CaptionStyle(BaseModel):
    """Burn-in caption styling. ``font`` is the brand font path (system fallback if missing).

    Captions are bottom-anchored; ``margin_*`` place them in the composition caption
    zone (defaults suit 1080x1920; the orchestrator computes them from composition).
    """

    font: Path | None = None
    font_size: int = 64
    primary_color: str = "FFFFFF"
    outline_color: str = "000000"
    outline: int = 3
    margin_v: int = 384  # gap from bottom edge -> caption_y_position 0.80 at 1080x1920
    margin_l: int = 90
    margin_r: int = 90


class TrackCrop(BaseModel):
    """A moving 9:16 crop window for the ``track`` reframe: full-height crop of width
    ``crop_w`` whose x follows ``x_expr`` (a time-varying ffmpeg expression)."""

    crop_w: int
    crop_h: int
    x_expr: str


class ClipRenderSpec(BaseModel):
    """A backend-agnostic description of one clip to render.

    Times are in SOURCE seconds; the backend cuts ``[start_sec, end_sec]`` from
    ``source``. ``captions`` carry clip-relative timing. ``watermark_path`` is
    skipped (with a warning) by the backend if the file is missing.
    """

    source: Path
    start_sec: float
    end_sec: float
    fmt: RenderFormat
    output_path: Path
    reframe_method: str = "blur_pad"
    foreground_scale: float = 1.0  # blur_pad only: enlarge the sharp band (see social_formats)
    track_crop: TrackCrop | None = None  # track only: moving 9:16 crop window
    zoom_crop_x: int | None = None  # zoom_crop only: subject-aware crop x (else geometric)
    captions: list[CaptionLine] = []
    caption_style: CaptionStyle = CaptionStyle()
    watermark_path: Path | None = None


@runtime_checkable
class RenderBackend(Protocol):
    """Produces a rendered clip file from a :class:`ClipRenderSpec`."""

    def render_clip(self, spec: ClipRenderSpec) -> Path:
        """Render one clip and return the written output path."""
        ...
