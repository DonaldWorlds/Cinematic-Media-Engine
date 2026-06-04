"""Thumbnail candidate extraction (frame selection + JPEG export).

Phase 7 (standard_longform / Descript-prep): pick the most aesthetic frames inside
the selected cut window using the cv2 aesthetics metrics (composition + lighting +
sharpness composite), then export them as JPEGs for Descript to use as thumbnail
source frames. The engine only EXTRACTS candidates — thumbnail rendering, text, and
branding happen downstream in Descript.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ..utils.ffmpeg import FFmpegError, run_ffmpeg
from ..utils.fileio import ensure_dir
from ..utils.logging import get_logger

_log = get_logger(__name__)

# The aesthetics metrics that compose the thumbnail score. color_harmony is tracked by
# the analysis layer but deliberately excluded — exposure, composition and focus drive
# a strong still frame (per CP1).
THUMBNAIL_METRICS = ("composition_score", "lighting_quality", "sharpness")


def thumbnail_composite(frame_metrics: Mapping[str, float]) -> float:
    """Composite frame score = composition + lighting + sharpness (missing keys -> 0)."""
    return float(sum(frame_metrics.get(name, 0.0) for name in THUMBNAIL_METRICS))


def select_thumbnail_frames(
    times_sec: Sequence[float],
    metrics: Mapping[str, Sequence[float]],
    *,
    start: float,
    end: float,
    count: int,
    min_gap_sec: float,
) -> list[float]:
    """Pick up to ``count`` timestamps in ``[start, end]`` by descending composite score.

    Frames within ``min_gap_sec`` of an already-chosen frame are skipped so the
    candidates are visually distinct. With no aesthetic data in the window, falls back
    to a single deterministic middle-of-window frame (never random — keeps it testable).
    """
    scored: list[tuple[float, float]] = []  # (composite, time)
    for i, t in enumerate(times_sec):
        if t < start or t > end:
            continue
        frame_metrics = {
            name: float(values[i]) for name, values in metrics.items() if i < len(values)
        }
        scored.append((thumbnail_composite(frame_metrics), t))

    if not scored:
        return [round((start + end) / 2.0, 3)]

    scored.sort(key=lambda s: (-s[0], s[1]))  # best first; ties -> earliest
    chosen: list[float] = []
    for _, t in scored:
        if all(abs(t - c) >= min_gap_sec for c in chosen):
            chosen.append(t)
        if len(chosen) >= count:
            break
    return chosen


def build_thumbnail_command(
    source: Path, time_sec: float, output_path: Path, *, width: int, height: int
) -> list[str]:
    """ffmpeg args (sans binary) to export one frame at ``time_sec`` as a JPEG."""
    return [
        "-y",
        "-ss",
        f"{time_sec:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:{height}",
        "-q:v",
        "2",  # high-quality JPEG
        str(output_path),
    ]


def extract_thumbnail_frame(
    source: Path, time_sec: float, output_path: Path, *, width: int, height: int
) -> Path:
    """Export a single JPEG frame at ``time_sec`` from ``source``; return its path."""
    ensure_dir(output_path.parent)
    run_ffmpeg(build_thumbnail_command(source, time_sec, output_path, width=width, height=height))
    return output_path


def extract_thumbnails(
    source: Path,
    times_sec: Sequence[float],
    output_dir: Path,
    *,
    width: int,
    height: int,
    source_name: str,
) -> list[Path]:
    """Export one JPEG per timestamp into ``output_dir``; return the written paths."""
    ensure_dir(output_dir)
    paths: list[Path] = []
    for rank, t in enumerate(times_sec, start=1):
        out = output_dir / f"{source_name}_thumb_{rank:02d}.jpg"
        try:
            paths.append(extract_thumbnail_frame(source, t, out, width=width, height=height))
        except FFmpegError as exc:  # one bad frame must not abort the run (SPEC §9)
            _log.warning("thumbnail: frame at %.3fs failed (%s); skipping", t, exc)
    _log.info("thumbnail: wrote %d candidate frame(s) to %s", len(paths), output_dir)
    return paths
