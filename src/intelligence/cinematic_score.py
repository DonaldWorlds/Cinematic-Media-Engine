"""Composite scoring for cinematic_longform mode (aesthetic / emotional arc).

Maps the six cinematic ``scoring_weights`` keys onto normalized signal timelines.
In Phase 3 (no OpenCLIP, no beat detection) ``openclip_aesthetic`` and
``soundtrack_sync`` are unavailable and their weight is redistributed across the
remaining signals (SPEC §9/§11.7). ``pacing_curve`` is an interim motion+energy
rhythm proxy (a placeholder like the cv2 aesthetics metrics), to be refined when
OpenCLIP/TwelveLabs arrive — see KNOWLEDGE.md.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .emotional_score import emotional_timeline
from .scoring import (
    ScoredTimeline,
    SignalData,
    make_grid,
    normalized_curve,
    redistribute_weights,
)


def blend_pacing(curves: Sequence[Sequence[float]]) -> list[float]:
    """Element-wise mean of the given normalized curves (the pacing-rhythm proxy)."""
    if not curves:
        return []
    length = len(curves[0])
    return [sum(c[i] for c in curves) / len(curves) for i in range(length)]


def build_cinematic_signals(data: SignalData, grid: Sequence[float]) -> dict[str, list[float]]:
    """Assemble the available cinematic signal timelines on ``grid``."""
    signals: dict[str, list[float]] = {}

    if data.audio_times:
        signals["emotional_moments"] = emotional_timeline(data.audio_times, data.energy, grid)

    if "lighting_quality" in data.aesthetics:
        signals["lighting_quality"] = normalized_curve(
            data.aesthetics_times, data.aesthetics["lighting_quality"], grid
        )
    if "composition_score" in data.aesthetics:
        signals["visual_composition"] = normalized_curve(
            data.aesthetics_times, data.aesthetics["composition_score"], grid
        )

    pacing_inputs: list[list[float]] = []
    if data.motion:
        pacing_inputs.append(normalized_curve(data.motion_times, data.motion, grid))
    if data.audio_times:
        pacing_inputs.append(normalized_curve(data.audio_times, data.energy, grid))
    if pacing_inputs:
        signals["pacing_curve"] = blend_pacing(pacing_inputs)

    return signals


def score_cinematic(data: SignalData, configured_weights: Mapping[str, float]) -> ScoredTimeline:
    """Build the cinematic composite timelines + effective (redistributed) weights."""
    grid = make_grid(data.duration_sec)
    signals = build_cinematic_signals(data, grid)
    weights = redistribute_weights(configured_weights, set(signals))
    unavailable = sorted(set(configured_weights) - set(signals))
    return ScoredTimeline(
        grid=grid,
        timelines=signals,
        weights=weights,
        configured_weights=dict(configured_weights),
        unavailable=unavailable,
    )
