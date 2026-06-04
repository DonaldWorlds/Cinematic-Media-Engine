"""Final ranked moment list and the moments.json artifact.

Takes the diversity-selected window candidates, drops anything below the mode's
score threshold (never lowered to force results — see KNOWLEDGE.md anti-pattern),
caps at the clip target, snaps boundaries to scene cuts when any exist, and emits
the ranked ``MomentsResult``. ``signal_contributions`` per moment sum to its
composite score for full tuning transparency.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from ..utils.fileio import write_json
from .clustering import WindowCandidate
from .pacing_engine import PacingPlan
from .scoring import ScoredTimeline


class ReframeDecision(BaseModel):
    """The auto-reframe choice for one moment — a render-layer diagnostic written back
    into moments.json for tuning visibility (mirrors the per-signal scoring breakdown).

    Populated by the render layer (it reads subjects.json); the intelligence layer never
    sets it, so this stays an optional annotation slot with no reverse dependency.
    ``median_count``/``max_spread`` are 0 for manual (non-auto) reframe methods.
    """

    method: str
    foreground_scale: float
    median_count: int
    max_spread: float


class Moment(BaseModel):
    """One ranked clip window with its per-signal contribution breakdown."""

    rank: int
    start_sec: float
    end_sec: float
    duration_sec: float
    composite_score: float
    scene_snapped: bool
    signal_contributions: dict[str, float]
    reframe: ReframeDecision | None = None  # set by the render layer (auto-reframe diagnostic)


class BestWindow(BaseModel):
    """The single strongest window, always reported (even when no moment clears the gate).

    ``signal_contributions`` are weight·mean (sum to ``composite_score``); ``signal_values``
    are the raw normalized windowed means — the lens for tuning weights over time.
    """

    start_sec: float
    end_sec: float
    composite_score: float
    signal_contributions: dict[str, float]
    signal_values: dict[str, float]


class WeightsReport(BaseModel):
    """Configured vs effective weights + which signals were unavailable this run."""

    configured: dict[str, float]
    effective: dict[str, float]
    unavailable_signals: list[str]


class DiversityReport(BaseModel):
    """Diversity constraints applied during selection."""

    min_gap_sec: float
    max_clips_per_minute: float | None


class MomentsResult(BaseModel):
    """The moments.json schema: ranked moments + the scoring context that produced them."""

    run_id: str
    mode: str
    source: str
    generated_at: str
    source_duration_sec: float
    window_sec: float
    composite_method: str
    score_threshold: float
    diversity: DiversityReport
    weights: WeightsReport
    # The strongest window, always populated — the tuning lens (per-signal breakdown)
    # regardless of whether anything cleared the gate.
    best_window: BestWindow
    moments: list[Moment]
    moment_count: int
    # True when no window cleared score_threshold; the engine then returns the top
    # candidates below as a fallback so every run yields usable output (never lower
    # the threshold to force results — see KNOWLEDGE.md anti-pattern).
    used_fallback: bool
    below_threshold_best_candidates: list[Moment]
    # Phase 7 (standard_longform / Descript-prep): thumbnail frame candidates +
    # short quotable text suggestions for the cut window. Additive (like ReframeDecision)
    # and written by the render layer; default empty / "not_generated" so shortform —
    # which never sets them — is unaffected. ``thumbnail_text_status`` distinguishes
    # empty-no-transcript from empty-no-quotable-content (see thumbnail_text.py).
    thumbnail_candidates: list[str] = []
    thumbnail_text_suggestions: list[str] = []
    thumbnail_text_status: str = "not_generated"


def snap_boundary(
    t: float, scene_cuts: Sequence[float], tolerance_sec: float
) -> tuple[float, bool]:
    """Snap ``t`` to the nearest scene cut within ``tolerance_sec`` (else leave it)."""
    if not scene_cuts:
        return t, False
    nearest = min(scene_cuts, key=lambda c: abs(c - t))
    if abs(nearest - t) <= tolerance_sec:
        return nearest, True
    return t, False


def rank_moments(
    candidates: Sequence[WindowCandidate],
    *,
    score_threshold: float,
    max_clips: int | None,
    scene_cuts: Sequence[float],
    snap_tolerance_sec: float,
) -> list[Moment]:
    """Threshold, cap, scene-snap and rank candidate windows into final moments."""
    kept = sorted(
        (c for c in candidates if c.score >= score_threshold),
        key=lambda c: c.score,
        reverse=True,
    )
    if max_clips is not None:
        kept = kept[:max_clips]

    moments: list[Moment] = []
    for rank, cand in enumerate(kept, start=1):
        start, snapped_start = snap_boundary(cand.start_sec, scene_cuts, snap_tolerance_sec)
        end, snapped_end = snap_boundary(cand.end_sec, scene_cuts, snap_tolerance_sec)
        moments.append(
            Moment(
                rank=rank,
                start_sec=start,
                end_sec=end,
                duration_sec=end - start,
                composite_score=cand.score,
                scene_snapped=snapped_start or snapped_end,
                signal_contributions=cand.contributions,
            )
        )
    return moments


def to_best_window(candidate: WindowCandidate) -> BestWindow:
    """Project the strongest candidate window into the always-reported BestWindow."""
    return BestWindow(
        start_sec=candidate.start_sec,
        end_sec=candidate.end_sec,
        composite_score=candidate.score,
        signal_contributions=candidate.contributions,
        signal_values=candidate.signal_values,
    )


def select_moments(
    candidates: Sequence[WindowCandidate],
    *,
    score_threshold: float,
    max_clips: int | None,
    top_n_fallback: int,
    scene_cuts: Sequence[float],
    snap_tolerance_sec: float,
) -> tuple[list[Moment], list[Moment], bool]:
    """Return ``(threshold_moments, fallback_candidates, used_fallback)``.

    Threshold-passers rank as usual. If none clear the gate, the top ``top_n_fallback``
    windows are returned as fallback candidates (never by lowering the threshold).
    """
    moments = rank_moments(
        candidates,
        score_threshold=score_threshold,
        max_clips=max_clips,
        scene_cuts=scene_cuts,
        snap_tolerance_sec=snap_tolerance_sec,
    )
    if moments:
        return moments, [], False
    fallback = rank_moments(
        candidates,
        score_threshold=float("-inf"),
        max_clips=top_n_fallback,
        scene_cuts=scene_cuts,
        snap_tolerance_sec=snap_tolerance_sec,
    )
    return [], fallback, True


def build_moments_result(
    *,
    run_id: str,
    mode: str,
    source: str,
    generated_at: str,
    source_duration_sec: float,
    composite_method: str,
    scored: ScoredTimeline,
    plan: PacingPlan,
    best_window: BestWindow,
    moments: Sequence[Moment],
    below_threshold_best_candidates: Sequence[Moment],
    used_fallback: bool,
) -> MomentsResult:
    """Assemble the full :class:`MomentsResult` from the scoring context + ranked moments."""
    return MomentsResult(
        run_id=run_id,
        mode=mode,
        source=source,
        generated_at=generated_at,
        source_duration_sec=source_duration_sec,
        window_sec=plan.window_sec,
        composite_method=composite_method,
        score_threshold=plan.score_threshold,
        diversity=DiversityReport(
            min_gap_sec=plan.min_gap_sec, max_clips_per_minute=plan.max_clips_per_minute
        ),
        weights=WeightsReport(
            configured=scored.configured_weights,
            effective=scored.weights,
            unavailable_signals=scored.unavailable,
        ),
        best_window=best_window,
        moments=list(moments),
        moment_count=len(moments),
        used_fallback=used_fallback,
        below_threshold_best_candidates=list(below_threshold_best_candidates),
    )


def write_moments(result: MomentsResult, dest: Path) -> Path:
    """Write ``result`` to the workspace ``moments.json`` (``dest``); return it."""
    write_json(dest, result.model_dump())
    return dest
