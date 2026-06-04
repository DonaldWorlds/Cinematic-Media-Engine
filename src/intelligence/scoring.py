"""Per-signal normalization and the generic composite primitives.

The intelligence layer scores moments on a shared per-second timeline. This
module supplies the building blocks every mode scorer reuses:

- ``normalize`` — per-run min-max to [0,1] (flat signal -> 0.0), so each signal
  is scored relative to its own range within the run (SPEC ``normalize_range``).
- ``make_grid`` / ``resample_curve`` — put every signal on one common 1-second
  grid so a window's score is just a slice average.
- ``redistribute_weights`` — when a weighted signal is unavailable (OpenCLIP,
  TwelveLabs, beat detection in Phase 3), drop it and renormalize the remaining
  weights to sum to 1.0 (SPEC §9 / §11.7).
- ``composite_timeline`` — weighted_sum of the normalized signals per grid point.

All functions are pure and unit-tested on synthetic data (SPEC §14.3).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from pydantic import BaseModel

# Whisper word-confidence floor: transcript words scoring below this are dropped
# from transcript-derived signals (viral_hooks, quotable_dialogue, transcript
# quality). Tunable here without a config-schema change; see KNOWLEDGE.md.
MIN_WORD_CONFIDENCE = 0.3


class WordSpan(BaseModel):
    """One transcript word's time span and alignment confidence (None = unaligned)."""

    start: float
    end: float
    score: float | None = None


class Span(BaseModel):
    """A start/end time span (a quotable line, a confident word region)."""

    start: float
    end: float


class SignalData(BaseModel):
    """Primitive signals extracted from the analysis artifacts for one run.

    The orchestrator reads the analysis JSON and fills this DTO with plain lists,
    so the intelligence layer never imports the heavy analysis modules (librosa,
    cv2). Empty source lists mean that signal was not produced this run.
    """

    duration_sec: float
    audio_times: list[float] = []
    energy: list[float] = []
    speech_intensity: list[float] = []
    peak_times: list[float] = []
    motion_times: list[float] = []
    motion: list[float] = []
    aesthetics_times: list[float] = []
    aesthetics: dict[str, list[float]] = {}
    has_transcript: bool = False
    words: list[WordSpan] = []
    hook_times: list[float] = []
    quotable_spans: list[Span] = []
    scene_cuts_sec: list[float] = []


class ScoredTimeline(BaseModel):
    """A mode's per-signal normalized timelines plus the weights to combine them.

    ``weights`` are effective (post-redistribution); ``configured_weights`` and
    ``unavailable`` are kept for transparency in moments.json.
    """

    grid: list[float]
    timelines: dict[str, list[float]]
    weights: dict[str, float]
    configured_weights: dict[str, float]
    unavailable: list[str]


def normalize(values: Sequence[float]) -> list[float]:
    """Min-max scale ``values`` to [0,1] over the run; a flat signal maps to 0.0."""
    if len(values) == 0:
        return []
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span <= 0.0:
        return [0.0] * len(values)
    return [(v - lo) / span for v in values]


def make_grid(duration_sec: float, step_sec: float = 1.0) -> list[float]:
    """Timestamps ``[0, step, 2*step, ...]`` covering ``[0, duration]`` inclusive."""
    if duration_sec <= 0.0:
        return [0.0]
    count = math.floor(duration_sec / step_sec)
    return [i * step_sec for i in range(count + 1)]


def resample_curve(
    times: Sequence[float], values: Sequence[float], grid: Sequence[float]
) -> list[float]:
    """Linear-interpolate a sampled (times, values) curve onto ``grid``.

    Empty input -> zeros; a single point -> that constant; ends flat-extrapolate.
    """
    if len(values) == 0 or len(times) == 0:
        return [0.0] * len(grid)
    interpolated = np.interp(np.asarray(grid, dtype=float), times, values)
    return [float(v) for v in interpolated]


def redistribute_weights(
    configured: Mapping[str, float], available: Iterable[str]
) -> dict[str, float]:
    """Keep only ``available`` keys and renormalize them to sum to 1.0.

    Preserves the relative proportions of the surviving weights. If none survive,
    returns an empty mapping.
    """
    available_set = set(available)
    kept = {k: v for k, v in configured.items() if k in available_set}
    total = sum(kept.values())
    if total <= 0.0:
        return {}
    return {k: v / total for k, v in kept.items()}


def normalized_curve(
    times: Sequence[float], values: Sequence[float], grid: Sequence[float]
) -> list[float]:
    """Resample a sampled curve onto ``grid`` then min-max normalize it to [0,1]."""
    return normalize(resample_curve(times, values, grid))


def influence_envelope(
    event_times: Sequence[float], grid: Sequence[float], radius_sec: float
) -> list[float]:
    """Triangular influence of point events: 1.0 at an event, linear to 0 at ±radius.

    Each grid point takes the max influence over all events (no events -> zeros).
    Gives sparse point events (peaks, hooks) temporal weight so a window CONTAINING
    one scores higher than one that doesn't.
    """
    if len(event_times) == 0 or radius_sec <= 0.0:
        return [0.0] * len(grid)
    out: list[float] = []
    for t in grid:
        influence = max(0.0, 1.0 - min(abs(t - e) for e in event_times) / radius_sec)
        out.append(influence)
    return out


def coverage_mask(spans: Sequence[tuple[float, float]], grid: Sequence[float]) -> list[float]:
    """1.0 at grid points falling inside any ``[start, end]`` span, else 0.0."""
    return [1.0 if any(start <= t <= end for start, end in spans) else 0.0 for t in grid]


def aggregate_window(
    values: Sequence[float], *, method: str = "mean", percentile: float = 90.0
) -> float:
    """Collapse a window's per-second signal values into one score.

    ``mean`` averages (dilutes spikes); ``percentile`` (e.g. 90th) captures the
    window's near-peak while ignoring single-frame noise — so windows containing a
    real highlight outscore flat ones instead of regressing to the mean. Empty -> 0.
    """
    if len(values) == 0:
        return 0.0
    if method == "percentile":
        return float(np.percentile(np.asarray(values, dtype=float), percentile))
    return sum(values) / len(values)


def composite_timeline(
    timelines: Mapping[str, Sequence[float]], weights: Mapping[str, float]
) -> list[float]:
    """Per-grid-point weighted sum over the keys present in ``weights``.

    Signals without a weight are ignored. Empty ``weights`` yields a zero timeline
    matching the grid length.
    """
    length = max((len(v) for v in timelines.values()), default=0)
    out = [0.0] * length
    for key, weight in weights.items():
        signal = timelines.get(key)
        if signal is None:
            continue
        for i, value in enumerate(signal):
            out[i] += weight * value
    return out


def score_standard(data: SignalData, configured_weights: Mapping[str, float]) -> ScoredTimeline:
    """Generic standard_longform composite (SPEC §1 defines no standard_score module).

    Maps the standard keys onto the signals available in Phase 3 and redistributes
    the rest: ``pacing_consistency`` (retention-steadiness) and ``narrative_continuity``
    (semantic) need TwelveLabs/later phases, so they are unavailable for now.
    """
    grid = make_grid(data.duration_sec)
    signals: dict[str, list[float]] = {}

    if data.audio_times:
        signals["audio_energy"] = normalized_curve(data.audio_times, data.energy, grid)
        signals["conversational"] = normalized_curve(data.audio_times, data.speech_intensity, grid)

    if data.has_transcript:
        confident_spans = [
            (w.start, w.end)
            for w in data.words
            if w.score is not None and w.score >= MIN_WORD_CONFIDENCE
        ]
        signals["transcript_quality"] = coverage_mask(confident_spans, grid)

    weights = redistribute_weights(configured_weights, set(signals))
    unavailable = sorted(set(configured_weights) - set(signals))
    return ScoredTimeline(
        grid=grid,
        timelines=signals,
        weights=weights,
        configured_weights=dict(configured_weights),
        unavailable=unavailable,
    )
