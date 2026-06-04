"""Mode-aware pacing rules → the windowing/diversity plan for clip selection.

Translates a mode's ``pacing`` / ``diversity`` config into the concrete
parameters the sliding-window selector needs: the fixed clip length, slide step,
minimum gap between selected clips, per-minute cap, score threshold and clip cap.

Shortform is locked to a 60s window (min=max=preferred=60); modes without a
preferred length fall back to ``max_segment_sec``. Diversity/threshold/clip-cap
fall back to the global ``scoring`` defaults when a mode does not override them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

# When no window clears the threshold, return this many top candidates as a
# fallback so every run yields usable output (modes may override via top_n_fallback).
DEFAULT_TOP_N_FALLBACK = 5


class PacingPlan(BaseModel):
    """Resolved windowing + diversity parameters for one mode."""

    window_sec: float
    step_sec: float
    min_gap_sec: float
    max_clips_per_minute: float | None
    score_threshold: float
    max_clips: int | None
    top_n_fallback: int
    window_aggregation: str
    window_percentile: float


def build_pacing_plan(
    pacing: Mapping[str, Any],
    *,
    diversity: Mapping[str, Any] | None = None,
    score_threshold: float | None = None,
    target_clips: int | None = None,
    top_n_fallback: int | None = None,
    window_aggregation: str = "mean",
    window_percentile: float = 90.0,
    default_min_gap_sec: float,
    default_threshold: float,
    step_sec: float = 1.0,
) -> PacingPlan:
    """Resolve a mode's pacing/diversity config into a concrete :class:`PacingPlan`."""
    window = pacing.get("preferred_duration_sec") or pacing.get("max_segment_sec")
    if window is None:
        raise ValueError("pacing must define preferred_duration_sec or max_segment_sec")

    min_gap = default_min_gap_sec
    max_cpm: float | None = None
    if diversity is not None:
        if "min_gap_sec" in diversity:
            min_gap = float(diversity["min_gap_sec"])
        if "max_clips_per_minute" in diversity:
            max_cpm = float(diversity["max_clips_per_minute"])

    return PacingPlan(
        window_sec=float(window),
        step_sec=step_sec,
        min_gap_sec=min_gap,
        max_clips_per_minute=max_cpm,
        score_threshold=score_threshold if score_threshold is not None else default_threshold,
        max_clips=target_clips,
        top_n_fallback=top_n_fallback if top_n_fallback is not None else DEFAULT_TOP_N_FALLBACK,
        window_aggregation=window_aggregation,
        window_percentile=window_percentile,
    )
