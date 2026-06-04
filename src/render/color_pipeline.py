"""Professional Color Pipeline — Stage 1 technical correction (Phase 10).

Window-scoped exposure / white-balance / contrast analysis that composes an ffmpeg
filter string (``eq`` -> ``colorbalance`` -> ``curves``) to prepend BEFORE the Phase-8
``lut3d`` grade. LUTs assume normalized input, so correction must precede the grade
(KNOWLEDGE.md "Phase 10 Candidate" — critical ordering rule).

Auto-detect by default; a mode's ``color_pipeline`` YAML may override any component.
Fail-loud: an unopenable / unsamplable source raises rather than silently skipping —
corrupt input would otherwise feed unpredictable colour into the LUT (KNOWLEDGE.md Q6).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from ..config import (
        ColorPipelineConfig,
        ContrastSettings,
        ExposureSettings,
        WhiteBalanceSettings,
    )

__all__ = [
    "analyze_contrast",
    "analyze_exposure",
    "analyze_white_balance",
    "build_correction_filter",
    "contrast_curve_points",
    "exposure_adjustment",
    "get_correction_for_mode",
    "gray_world_gains",
    "kelvin_to_gains",
    "window_sample_times",
]

_EPS = 1e-6
_MIDGRAY = 0.5  # exposure target (mean luma), v1 — KNOWLEDGE.md Phase 10 Q5
_WB_REFERENCE_KELVIN = 5600  # ~D65 neutral target for override-by-kelvin
_DEFAULT_SAMPLE_FRAMES = 30

# Override contrast presets -> normalized curve control points (stronger = more stretch).
_CONTRAST_PRESETS: dict[str, dict[str, float]] = {
    "soft": {"shadow_lift": 0.02, "midtone": 0.5, "highlight_roll": 0.98},
    "medium": {"shadow_lift": 0.06, "midtone": 0.5, "highlight_roll": 0.94},
    "high": {"shadow_lift": 0.12, "midtone": 0.5, "highlight_roll": 0.88},
}


# --------------------------------------------------------------------------- #
# Pure math helpers (deterministic, no I/O)
# --------------------------------------------------------------------------- #


def window_sample_times(
    window_start: float, window_end: float, sample_frames: int, *, duration: float
) -> list[float]:
    """Evenly-distributed timestamps strictly inside the window clamped to ``[0, duration]``."""
    start = max(0.0, window_start)
    end = min(window_end, duration)
    if end <= start:
        raise ValueError(
            f"empty sampling window after clamp: [{start:.3f}, {end:.3f}] (duration={duration:.3f})"
        )
    span = end - start
    return [start + (i + 0.5) * span / sample_frames for i in range(sample_frames)]


def exposure_adjustment(mean_luma: float) -> float:
    """Brightness delta to pull mean luma toward mid-gray, clamped to the ffmpeg eq range."""
    return float(max(-1.0, min(1.0, _MIDGRAY - mean_luma)))


def gray_world_gains(mean_rgb: tuple[float, float, float]) -> dict[str, float]:
    """Per-channel gains equalizing mean R/G/B toward neutral (gray-world; epsilon-guarded)."""
    r = max(_EPS, float(mean_rgb[0]))
    g = max(_EPS, float(mean_rgb[1]))
    b = max(_EPS, float(mean_rgb[2]))
    gray = (r + g + b) / 3.0
    return {"red_gain": gray / r, "green_gain": gray / g, "blue_gain": gray / b}


def contrast_curve_points(p5: float, p50: float, p95: float) -> dict[str, float]:
    """Normalize measured luma percentiles into monotonic curve control points in ``[0,1]``."""
    lo = float(max(0.0, min(1.0, p5)))
    hi = float(max(0.0, min(1.0, p95)))
    mid = float(max(lo, min(hi, max(0.0, min(1.0, p50)))))
    return {"shadow_lift": lo, "midtone": mid, "highlight_roll": hi}


def _kelvin_rgb(kelvin: int) -> tuple[float, float, float]:
    """Approximate normalized RGB of a blackbody at ``kelvin`` (Tanner Helland)."""
    t = kelvin / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661 if t > 0 else 0.0
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    rn = max(0.0, min(255.0, r)) / 255.0
    gn = max(0.0, min(255.0, g)) / 255.0
    bn = max(0.0, min(255.0, b)) / 255.0
    return (rn, gn, bn)


def _sum_normalize(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Scale an RGB triple to unit channel-sum so warmth shows as the R:B balance."""
    total = max(_EPS, rgb[0] + rgb[1] + rgb[2])
    return (rgb[0] / total, rgb[1] / total, rgb[2] / total)


def kelvin_to_gains(kelvin: int) -> dict[str, float]:
    """Gains to neutralize a source assumed shot at ``kelvin`` toward the ~5600K reference.

    Sum-normalize both blackbody triples before the per-channel ratio: the Tanner-Helland
    red channel saturates at 255 for all temps <=6600K, so a raw ``ref/src`` ratio would
    read ``red_gain == 1`` for every warm source. The shared sum-rescale restores the warm
    cast (red_gain < 1, blue_gain > 1) while keeping the reference temperature at unity.
    """
    ref = _sum_normalize(_kelvin_rgb(_WB_REFERENCE_KELVIN))
    src = _sum_normalize(_kelvin_rgb(kelvin))
    return {
        "red_gain": ref[0] / max(_EPS, src[0]),
        "green_gain": ref[1] / max(_EPS, src[1]),
        "blue_gain": ref[2] / max(_EPS, src[2]),
    }


def _fmt(value: float) -> str:
    """Compact fixed-point for filter strings (trims trailing zeros; ``0.0`` -> ``0``)."""
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _gain_to_midtone_shift(gain: float) -> float:
    """Linear gain -> colorbalance midtone shift in ``[-1,1]`` (v1 approximation)."""
    return max(-1.0, min(1.0, gain - 1.0))


# --------------------------------------------------------------------------- #
# Frame sampling I/O (fail-loud; monkeypatch seams for window tests)
# --------------------------------------------------------------------------- #


def _probe_duration(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video for colour analysis: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps <= 0 or frames <= 0:
            raise RuntimeError(f"cannot determine duration for colour analysis: {video_path}")
        # Last decodable-frame timestamp, not nominal duration: seeking past the final
        # frame yields no frame, so bound sampling to (frame_count - 1) / fps.
        return max(0.0, float(frames) - 1.0) / float(fps)
    finally:
        cap.release()


def _grab_frames(video_path: Path, times: Sequence[float]) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(video_path))
    frames: list[np.ndarray] = []
    try:
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video for colour analysis: {video_path}")
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(f"failed to sample frame at {t:.3f}s in {video_path}")
            frames.append(frame)
    finally:
        cap.release()
    return frames


def _sample_window(
    video_path: Path, window_start_sec: float, window_end_sec: float, sample_frames: int
) -> list[np.ndarray]:
    duration = _probe_duration(video_path)
    times = window_sample_times(window_start_sec, window_end_sec, sample_frames, duration=duration)
    return _grab_frames(video_path, times)


# --------------------------------------------------------------------------- #
# Analyzers (window-scoped; BGR frames from OpenCV)
# --------------------------------------------------------------------------- #


def analyze_exposure(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    sample_frames: int = _DEFAULT_SAMPLE_FRAMES,
) -> float:
    """Mean-luma exposure delta within the window; ``0.0`` == already at mid-gray."""
    frames = _sample_window(video_path, window_start_sec, window_end_sec, sample_frames)
    lumas = [float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()) / 255.0 for f in frames]
    return exposure_adjustment(sum(lumas) / len(lumas))


def analyze_white_balance(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    sample_frames: int = _DEFAULT_SAMPLE_FRAMES,
) -> dict[str, float]:
    """Gray-world RGB gain targets within the window (neutral D65 white point)."""
    frames = _sample_window(video_path, window_start_sec, window_end_sec, sample_frames)
    per_frame = np.stack([f.reshape(-1, 3).mean(axis=0) for f in frames])  # BGR
    channel_means = per_frame.mean(axis=0)
    b = float(channel_means[0])
    g = float(channel_means[1])
    r = float(channel_means[2])
    return gray_world_gains((r, g, b))


def analyze_contrast(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    sample_frames: int = _DEFAULT_SAMPLE_FRAMES,
) -> dict[str, float]:
    """Luma 5th/50th/95th-percentile curve points within the window (dynamic-range normalize)."""
    frames = _sample_window(video_path, window_start_sec, window_end_sec, sample_frames)
    lumas = (
        np.concatenate([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).reshape(-1) for f in frames]).astype(
            np.float64
        )
        / 255.0
    )
    p5 = float(np.percentile(lumas, 5))
    p50 = float(np.percentile(lumas, 50))
    p95 = float(np.percentile(lumas, 95))
    return contrast_curve_points(p5, p50, p95)


# --------------------------------------------------------------------------- #
# Filter composition + public entry point
# --------------------------------------------------------------------------- #


def build_correction_filter(
    exposure: float | None,
    wb: dict[str, float] | None,
    contrast: dict[str, float] | None,
) -> str:
    """Compose ``eq,colorbalance,curves`` (in that order) from resolved correction values.

    Returns ``""`` when every component is ``None``. White balance uses the colorbalance
    MIDTONE band (``rm/gm/bm``) — most subject content (skin tones) sits in midtones, not
    shadows; this deviates from a shadows-band correction by design (KNOWLEDGE.md Phase 10).
    """
    parts: list[str] = []
    if exposure is not None:
        parts.append(f"eq=brightness={_fmt(exposure)}")
    if wb is not None:
        rm = _gain_to_midtone_shift(wb["red_gain"])
        gm = _gain_to_midtone_shift(wb["green_gain"])
        bm = _gain_to_midtone_shift(wb["blue_gain"])
        parts.append(f"colorbalance=rm={_fmt(rm)}:gm={_fmt(gm)}:bm={_fmt(bm)}")
    if contrast is not None:
        sl = _fmt(contrast["shadow_lift"])
        mt = _fmt(contrast["midtone"])
        hr = _fmt(contrast["highlight_roll"])
        parts.append(f"curves=all='{sl}/0 {mt}/0.5 {hr}/1'")
    return ",".join(parts)


def _resolve_exposure(
    settings: ExposureSettings,
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
) -> float | None:
    if settings.mode == "override":
        return settings.override_value
    return analyze_exposure(video_path, window_start_sec, window_end_sec)


def _resolve_wb(
    settings: WhiteBalanceSettings,
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
) -> dict[str, float] | None:
    if settings.mode == "override":
        return kelvin_to_gains(settings.override_kelvin) if settings.override_kelvin else None
    return analyze_white_balance(video_path, window_start_sec, window_end_sec)


def _resolve_contrast(
    settings: ContrastSettings,
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
) -> dict[str, float] | None:
    if settings.mode == "override":
        return dict(_CONTRAST_PRESETS[settings.override_curve]) if settings.override_curve else None
    return analyze_contrast(video_path, window_start_sec, window_end_sec)


def get_correction_for_mode(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    color_pipeline_config: ColorPipelineConfig,
) -> str:
    """Window-tuned correction filter for a mode, or ``""`` when correction is disabled.

    Auto components sample ``video_path`` within ``[window_start_sec, window_end_sec]``;
    override components skip sampling entirely. Raises on an unanalyzable source.
    """
    tc = color_pipeline_config.technical_correction
    if not tc.enabled:
        return ""
    exposure = _resolve_exposure(tc.exposure, video_path, window_start_sec, window_end_sec)
    wb = _resolve_wb(tc.white_balance, video_path, window_start_sec, window_end_sec)
    contrast = _resolve_contrast(tc.contrast, video_path, window_start_sec, window_end_sec)
    return build_correction_filter(exposure, wb, contrast)
