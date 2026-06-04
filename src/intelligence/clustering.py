"""Candidate-window generation and diversity selection.

Implements the approved selection model: slide a fixed-length window over the
mode's composite timeline, score each window as the MEAN composite inside it
(so ranking favours the strongest 60s *window*, not a peak instant), then greedily
keep non-overlapping windows by score subject to a minimum gap and an optional
per-minute cap (SPEC §11.8 — clustering forces diversity). Sources shorter than
the window collapse to a single clamped window over the whole source.

To change the windowing strategy (anchor-on-peaks, cluster-first) or the mean-vs-
max aggregation, this is the file — see KNOWLEDGE.md Phase-3 design decisions.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from .scoring import ScoredTimeline, aggregate_window


class WindowCandidate(BaseModel):
    """One scored candidate window with its per-signal breakdown.

    ``contributions`` are weight·mean (sum to ``score``); ``signal_values`` are the
    raw normalized windowed means (pre-weight) kept for weight-tuning visibility.
    """

    start_sec: float
    end_sec: float
    score: float
    contributions: dict[str, float]
    signal_values: dict[str, float] = {}


def _window_starts(duration_sec: float, window_sec: float, step_sec: float) -> list[float]:
    if duration_sec <= window_sec:
        return [0.0]
    last = duration_sec - window_sec
    starts: list[float] = []
    s = 0.0
    while s < last - 1e-9:
        starts.append(s)
        s += step_sec
    starts.append(last)
    return starts


def _window_slice(
    values: Sequence[float], grid: Sequence[float], start: float, end: float
) -> list[float]:
    return [v for v, t in zip(values, grid, strict=False) if start <= t <= end]


def slide_windows(
    scored: ScoredTimeline,
    *,
    window_sec: float,
    step_sec: float,
    duration_sec: float,
    method: str = "mean",
    percentile: float = 90.0,
) -> list[WindowCandidate]:
    """Score every fixed-length window by aggregating each signal's grid slice.

    ``method`` defaults to ``mean`` (backward-compatible); production passes
    ``percentile`` (Phase 3.5) so a window's near-peak survives instead of
    regressing to the mean. ``signal_values`` holds the aggregated per-signal value.
    """
    candidates: list[WindowCandidate] = []
    for start in _window_starts(duration_sec, window_sec, step_sec):
        end = min(start + window_sec, duration_sec)
        values = {
            key: aggregate_window(
                _window_slice(scored.timelines[key], scored.grid, start, end),
                method=method,
                percentile=percentile,
            )
            for key in scored.weights
            if key in scored.timelines
        }
        contributions = {key: scored.weights[key] * value for key, value in values.items()}
        candidates.append(
            WindowCandidate(
                start_sec=start,
                end_sec=end,
                score=sum(contributions.values()),
                contributions=contributions,
                signal_values=values,
            )
        )
    return candidates


def select_diverse(
    candidates: Sequence[WindowCandidate],
    *,
    min_gap_sec: float,
    max_clips_per_minute: float | None,
) -> list[WindowCandidate]:
    """Greedily keep the highest-scoring windows that respect diversity constraints."""
    accepted: list[WindowCandidate] = []
    for cand in sorted(candidates, key=lambda c: c.score, reverse=True):
        if any(abs(cand.start_sec - a.start_sec) < min_gap_sec for a in accepted):
            continue
        if max_clips_per_minute is not None:
            in_minute = sum(1 for a in accepted if abs(a.start_sec - cand.start_sec) < 60.0)
            if in_minute >= max_clips_per_minute:
                continue
        accepted.append(cand)
    return accepted
