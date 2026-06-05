"""Professional Color Pipeline — Stage 1 pre-LUT technical BALANCE (CP1).

Window-scoped exposure / white-balance analysis that composes an ffmpeg filter string
(``eq`` -> ``colorbalance``) prepended BEFORE the Phase-8 ``lut3d`` grade.

CP1 principle (replaces the old "LUTs assume normalized input" premise, which was wrong
for a log->Rec.709 conversion LUT and produced blown-out white):

    A pre-LUT correction BALANCES the log signal so the LUT receives clean input. It must
    NOT do the dynamic-range expansion the LUT itself does — the LUT owns contrast. So the
    pre-LUT stage is exposure-balance + white-balance only, each a bounded NUDGE. There is
    NO contrast curve here (a full-range stretch on flat log + the LUT's own expansion is
    what clipped everything to white). Any contrast trim belongs AFTER the LUT, separately.

Auto-detect by default; a mode's ``color_pipeline`` YAML may override either component.
Fail-loud: an unopenable / unsamplable source raises rather than silently skipping —
corrupt input would otherwise feed unpredictable colour into the LUT (KNOWLEDGE.md Q6).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from ..config import ColorPipelineConfig

__all__ = [
    "CorrectionResult",
    "analyze_exposure",
    "analyze_white_balance",
    "build_correction_filter",
    "exposure_adjustment",
    "get_correction_for_mode",
    "gray_world_gains",
    "kelvin_to_gains",
    "resolve_correction",
    "window_sample_times",
]

_EPS = 1e-6
_DEFAULT_TARGET_MIDGRAY = 0.41  # S-Log3 18% gray (~code 420/1023); balance auto-exposure here
_DEFAULT_MAX_SHIFT = 0.10  # gentle bound on the exposure/WB nudge (balance, not slam/normalize)
_WB_REFERENCE_KELVIN = 5600  # ~D65 neutral target for override-by-kelvin
_DEFAULT_SAMPLE_FRAMES = 30


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


def exposure_adjustment(
    mean_luma: float,
    target: float = _DEFAULT_TARGET_MIDGRAY,
    max_shift: float = _DEFAULT_MAX_SHIFT,
) -> float:
    """Bounded brightness delta nudging ``mean_luma`` toward the log mid-gray ``target``.

    Balance, not normalize: the additive delta is clamped to ``[-max_shift, max_shift]`` so a
    flat log image is gently re-centred for the LUT rather than slammed onto ``target``.
    """
    return float(max(-max_shift, min(max_shift, target - mean_luma)))


def gray_world_gains(mean_rgb: tuple[float, float, float]) -> dict[str, float]:
    """Per-channel gains equalizing mean R/G/B toward neutral (gray-world; epsilon-guarded)."""
    r = max(_EPS, float(mean_rgb[0]))
    g = max(_EPS, float(mean_rgb[1]))
    b = max(_EPS, float(mean_rgb[2]))
    gray = (r + g + b) / 3.0
    return {"red_gain": gray / r, "green_gain": gray / g, "blue_gain": gray / b}


def _kelvin_rgb(kelvin: int) -> tuple[float, float, float]:
    """Approximate normalized RGB of a blackbody at ``kelvin`` (Tanner Helland)."""
    import math

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


def _gain_to_midtone_shift(gain: float, max_shift: float = _DEFAULT_MAX_SHIFT) -> float:
    """Linear gain -> bounded colorbalance midtone shift; clamped to ``[-max_shift, max_shift]``."""
    return max(-max_shift, min(max_shift, gain - 1.0))


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
# Frame -> stat reductions (pure; BGR frames from OpenCV). Cheap: no float64 expansion.
# --------------------------------------------------------------------------- #


def _mean_luma(frames: Sequence[np.ndarray]) -> float:
    """Mean luma in ``[0,1]`` across ``frames`` (per-frame grayscale mean, then averaged)."""
    lumas = [float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()) / 255.0 for f in frames]
    return sum(lumas) / len(lumas)


def _channel_means_rgb(frames: Sequence[np.ndarray]) -> tuple[float, float, float]:
    """Mean ``(R, G, B)`` across ``frames`` (OpenCV decodes BGR; reorder on the way out)."""
    per_frame = np.stack([f.reshape(-1, 3).mean(axis=0) for f in frames])  # BGR
    channel_means = per_frame.mean(axis=0)
    return (float(channel_means[2]), float(channel_means[1]), float(channel_means[0]))


# --------------------------------------------------------------------------- #
# Analyzers (window-scoped; sample then reduce). Kept as standalone public helpers.
# --------------------------------------------------------------------------- #


def analyze_exposure(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    sample_frames: int = _DEFAULT_SAMPLE_FRAMES,
) -> float:
    """Bounded exposure-balance delta within the window (toward the default log mid-gray)."""
    frames = _sample_window(video_path, window_start_sec, window_end_sec, sample_frames)
    return exposure_adjustment(_mean_luma(frames))


def analyze_white_balance(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    sample_frames: int = _DEFAULT_SAMPLE_FRAMES,
) -> dict[str, float]:
    """Gray-world RGB gain targets within the window (neutral D65 white point)."""
    frames = _sample_window(video_path, window_start_sec, window_end_sec, sample_frames)
    return gray_world_gains(_channel_means_rgb(frames))


# --------------------------------------------------------------------------- #
# Filter composition + structured result + public entry point
# --------------------------------------------------------------------------- #


def build_correction_filter(
    exposure: float | None,
    wb: dict[str, float] | None,
    *,
    wb_max_shift: float = _DEFAULT_MAX_SHIFT,
) -> str:
    """Compose ``eq,colorbalance`` (in that order) from resolved balance values.

    Returns ``""`` when both components are ``None``. NO contrast curve is ever emitted —
    the LUT owns dynamic range (CP1). White balance uses the colorbalance MIDTONE band
    (``rm/gm/bm``) — most subject content (skin tones) sits in midtones — each shift clamped
    to ``wb_max_shift`` so the balance stays a gentle nudge.
    """
    parts: list[str] = []
    if exposure is not None:
        parts.append(f"eq=brightness={_fmt(exposure)}")
    if wb is not None:
        rm = _gain_to_midtone_shift(wb["red_gain"], wb_max_shift)
        gm = _gain_to_midtone_shift(wb["green_gain"], wb_max_shift)
        bm = _gain_to_midtone_shift(wb["blue_gain"], wb_max_shift)
        parts.append(f"colorbalance=rm={_fmt(rm)}:gm={_fmt(gm)}:bm={_fmt(bm)}")
    return ",".join(parts)


class CorrectionResult(BaseModel):
    """Resolved pre-LUT balance: the ffmpeg filter string + the measured/computed values
    behind it, for the auditable grade report. ``contrast_normalization`` is always ``False``
    (the LUT owns contrast) — recorded explicitly so a regression is visible in the report."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    filter_string: str
    contrast_normalization: bool = False
    mean_luma: float | None = None
    channel_means_rgb: tuple[float, float, float] | None = None
    target_midgray: float | None = None
    exposure_offset: float | None = None
    wb_shifts: dict[str, float] | None = None


def resolve_correction(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    color_pipeline_config: ColorPipelineConfig,
) -> CorrectionResult:
    """Resolve the pre-LUT balance for a mode into a ``CorrectionResult``.

    Samples the window AT MOST ONCE and feeds both exposure and white balance from the same
    frames (no double-decode of 4K). Auto components sample; override components skip sampling.
    Disabled correction returns an empty, unsampled result. Raises on an unanalyzable source.
    """
    tc = color_pipeline_config.technical_correction
    if not tc.enabled:
        return CorrectionResult(enabled=False, filter_string="")

    exp = tc.exposure
    wb = tc.white_balance
    need_sample = exp.mode == "auto" or wb.mode == "auto"
    frames = (
        _sample_window(video_path, window_start_sec, window_end_sec, _DEFAULT_SAMPLE_FRAMES)
        if need_sample
        else None
    )

    mean_luma: float | None = None
    if exp.mode == "override":
        exposure_offset = exp.override_value
        target_midgray = None
    else:
        assert frames is not None
        mean_luma = _mean_luma(frames)
        exposure_offset = exposure_adjustment(mean_luma, exp.target_midgray, exp.max_shift)
        target_midgray = exp.target_midgray

    channel_means: tuple[float, float, float] | None = None
    if wb.mode == "override":
        wb_gains = kelvin_to_gains(wb.override_kelvin) if wb.override_kelvin else None
    else:
        assert frames is not None
        channel_means = _channel_means_rgb(frames)
        wb_gains = gray_world_gains(channel_means)

    filter_string = build_correction_filter(exposure_offset, wb_gains, wb_max_shift=wb.max_shift)
    wb_shifts = (
        {
            "rm": _gain_to_midtone_shift(wb_gains["red_gain"], wb.max_shift),
            "gm": _gain_to_midtone_shift(wb_gains["green_gain"], wb.max_shift),
            "bm": _gain_to_midtone_shift(wb_gains["blue_gain"], wb.max_shift),
        }
        if wb_gains is not None
        else None
    )
    return CorrectionResult(
        enabled=True,
        filter_string=filter_string,
        contrast_normalization=False,
        mean_luma=mean_luma,
        channel_means_rgb=channel_means,
        target_midgray=target_midgray,
        exposure_offset=exposure_offset,
        wb_shifts=wb_shifts,
    )


def get_correction_for_mode(
    video_path: Path,
    window_start_sec: float,
    window_end_sec: float,
    color_pipeline_config: ColorPipelineConfig,
) -> str:
    """Window-tuned pre-LUT balance filter for a mode, or ``""`` when correction is disabled.

    Thin wrapper over :func:`resolve_correction` returning just its filter string (the cut
    path consumes this); ``run_grade`` calls ``resolve_correction`` directly for the report.
    """
    return resolve_correction(
        video_path, window_start_sec, window_end_sec, color_pipeline_config
    ).filter_string
