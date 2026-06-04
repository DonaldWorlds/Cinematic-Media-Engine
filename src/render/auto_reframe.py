"""Auto-reframe decision engine (Phase 4.5 Layer 2).

Reads the per-frame content profile (``analysis/subjects.json``) for one clip's
time window, aggregates it (median subject_count + max spread_ratio), and picks the
reframe method + foreground_scale via discrete buckets. Solo clips get ``track`` (a
9:16 window that follows the subject, smoothed with a one-euro filter). All pure +
unit-tested; the ffmpeg crop-path is a piecewise-linear ``x(t)`` expression.

reframe_method: auto runs this; manual track/blur_pad/center_crop still override.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from pydantic import BaseModel

from ..analysis.subjects import FrameSubjects
from ..config import ReframeDecisionConfig
from .backend import TrackCrop

# one-euro filter params for the track crop path (tunable; iterate if track looks off).
ONE_EURO_MIN_CUTOFF = 0.5
ONE_EURO_BETA = 0.007
ONE_EURO_D_CUTOFF = 1.0
# Cap track keyframes so the ffmpeg x(t) expression stays bounded.
MAX_TRACK_KEYFRAMES = 24


class WindowProfile(BaseModel):
    """Aggregated content profile for one clip window."""

    median_count: int
    max_spread: float
    frame_count: int


class ReframeChoice(BaseModel):
    """The method + foreground_scale a decision branch selects."""

    method: str
    foreground_scale: float


class ReframePlan(BaseModel):
    """Full reframe plan for one clip (track_crop set only for the ``track`` method)."""

    method: str
    foreground_scale: float
    track_crop: TrackCrop | None = None


def frames_in_window(
    frames: Sequence[FrameSubjects], start_sec: float, end_sec: float
) -> list[FrameSubjects]:
    """Subset of detection frames whose timestamp falls in ``[start, end]``."""
    return [f for f in frames if start_sec <= f.time_sec <= end_sec]


def aggregate_window(frames: Sequence[FrameSubjects]) -> WindowProfile:
    """Median subject_count (robust to flicker) + max spread_ratio (conservative guard)."""
    if not frames:
        return WindowProfile(median_count=0, max_spread=0.0, frame_count=0)
    median_count = round(statistics.median([f.subject_count for f in frames]))
    return WindowProfile(
        median_count=median_count,
        max_spread=max(f.spread_ratio for f in frames),
        frame_count=len(frames),
    )


def decide_reframe(
    median_count: int, max_spread: float, cfg: ReframeDecisionConfig
) -> ReframeChoice:
    """Pick reframe method + foreground_scale from the aggregated profile (discrete buckets)."""
    if median_count == 1 and max_spread <= cfg.solo_track_max_width:
        return ReframeChoice(method="track", foreground_scale=1.0)
    if median_count == 2:
        return ReframeChoice(method="blur_pad", foreground_scale=cfg.bucket_2_fs)
    if 3 <= median_count <= cfg.small_group_max:
        return ReframeChoice(method="blur_pad", foreground_scale=cfg.bucket_3_5_fs)
    # 0 people, solo-but-too-wide, or large group -> keep everyone, accept bars
    return ReframeChoice(method="blur_pad", foreground_scale=cfg.bucket_6_plus_fs)


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


def one_euro_smooth(
    times: Sequence[float],
    values: Sequence[float],
    *,
    min_cutoff: float = ONE_EURO_MIN_CUTOFF,
    beta: float = ONE_EURO_BETA,
    d_cutoff: float = ONE_EURO_D_CUTOFF,
) -> list[float]:
    """One-euro filter: smooths jitter when steady, stays responsive when moving."""
    out: list[float] = []
    x_prev: float | None = None
    x_hat_prev = 0.0
    dx_hat_prev = 0.0
    t_prev = 0.0
    for t, x in zip(times, values, strict=False):
        if x_prev is None:
            x_hat = x
        else:
            dt = max(t - t_prev, 1e-6)
            dx = (x - x_prev) / dt
            a_d = _alpha(d_cutoff, dt)
            dx_hat = a_d * dx + (1.0 - a_d) * dx_hat_prev
            cutoff = min_cutoff + beta * abs(dx_hat)
            a = _alpha(cutoff, dt)
            x_hat = a * x + (1.0 - a) * x_hat_prev
            dx_hat_prev = dx_hat
        out.append(x_hat)
        x_prev = x
        x_hat_prev = x_hat
        t_prev = t
    return out


def track_crop_path(
    centers: Sequence[tuple[float, float]],
    *,
    crop_w: int,
    frame_w: int,
) -> list[tuple[float, float]]:
    """Smooth the subject x-centres and convert to a clamped crop-x path ``[(t, x)]``."""
    times = [t for t, _ in centers]
    smoothed = one_euro_smooth(times, [x for _, x in centers])
    max_x = float(frame_w - crop_w)
    return [
        (t, min(max(x - crop_w / 2.0, 0.0), max_x)) for t, x in zip(times, smoothed, strict=False)
    ]


def _downsample(path: Sequence[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
    if len(path) <= max_points:
        return list(path)
    idxs = sorted({round(i * (len(path) - 1) / (max_points - 1)) for i in range(max_points)})
    return [path[i] for i in idxs]


def crop_x_expr(path: Sequence[tuple[float, float]]) -> str:
    """Piecewise-linear ffmpeg ``x(t)`` expression from a ``[(t, x)]`` keyframe path."""
    if not path:
        return "0.000"
    if len(path) == 1:
        return f"{path[0][1]:.3f}"
    expr = f"{path[-1][1]:.3f}"
    for i in range(len(path) - 2, -1, -1):
        t0, x0 = path[i]
        t1, x1 = path[i + 1]
        seg = f"{x0:.3f}+({x1:.3f}-{x0:.3f})*(t-{t0:.3f})/({t1:.3f}-{t0:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def _even(value: float) -> int:
    return 2 * round(value / 2)


def plan_clip(
    window_frames: Sequence[FrameSubjects],
    *,
    clip_start_sec: float,
    src_width: int,
    src_height: int,
    target_width: int,
    target_height: int,
    cfg: ReframeDecisionConfig,
) -> ReframePlan:
    """Aggregate one clip's detection window into a concrete reframe plan."""
    profile = aggregate_window(window_frames)
    choice = decide_reframe(profile.median_count, profile.max_spread, cfg)
    if choice.method != "track":
        return ReframePlan(method=choice.method, foreground_scale=choice.foreground_scale)

    centers = [
        (f.time_sec - clip_start_sec, (f.group_bbox[0] + f.group_bbox[2]) / 2.0)
        for f in window_frames
        if f.group_bbox is not None
    ]
    if not centers:  # solo decided but no boxes this window -> safe fallback
        return ReframePlan(method="blur_pad", foreground_scale=cfg.bucket_6_plus_fs)
    crop_w = _even(src_height * target_width / target_height)
    path = _downsample(
        track_crop_path(centers, crop_w=crop_w, frame_w=src_width), MAX_TRACK_KEYFRAMES
    )
    return ReframePlan(
        method="track",
        foreground_scale=1.0,
        track_crop=TrackCrop(crop_w=crop_w, crop_h=src_height, x_expr=crop_x_expr(path)),
    )
