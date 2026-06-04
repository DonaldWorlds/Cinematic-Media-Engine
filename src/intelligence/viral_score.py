"""Composite scoring for shortform mode (viral potential).

Maps the six shortform ``scoring_weights`` keys onto normalized signal timelines
on the shared per-second grid, then redistributes weight off any unavailable
signal (SPEC §9/§11.7). Transcript-derived signals (viral_hooks,
quotable_dialogue) are word-confidence gated: a hook counts only if it lands on a
word scoring >= ``MIN_WORD_CONFIDENCE``, and a quotable counts only words above
that floor (dropped if too few remain). See KNOWLEDGE.md for the design rationale.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .emotional_score import emotional_timeline
from .scoring import (
    MIN_WORD_CONFIDENCE,
    ScoredTimeline,
    SignalData,
    Span,
    WordSpan,
    coverage_mask,
    influence_envelope,
    make_grid,
    normalized_curve,
    redistribute_weights,
)

# Temporal influence radii: how far (sec) a point event spreads so a 60s window
# CONTAINING it scores above one that doesn't. Placeholders to tune on real
# footage (see KNOWLEDGE.md anti-pattern: don't tune before seeing real output).
PEAK_INFLUENCE_RADIUS_SEC = 5.0
HOOK_INFLUENCE_RADIUS_SEC = 5.0
# Mirrors hooks.DEFAULT_MIN_QUOTABLE_WORDS — a quotable needs this many confident words.
MIN_QUOTABLE_WORDS = 5


def confident_word_spans(words: Sequence[WordSpan], min_conf: float) -> list[Span]:
    """Time spans of words whose alignment confidence is at or above ``min_conf``."""
    return [
        Span(start=w.start, end=w.end) for w in words if w.score is not None and w.score >= min_conf
    ]


def gate_hook_times(hook_times: Sequence[float], confident_spans: Sequence[Span]) -> list[float]:
    """Keep only hook timestamps that land on a confident word span."""
    return [t for t in hook_times if any(s.start <= t <= s.end for s in confident_spans)]


def gate_quotable_spans(
    quotable_spans: Sequence[Span],
    words: Sequence[WordSpan],
    *,
    min_conf: float,
    min_words: int,
) -> list[Span]:
    """Keep quotables that still contain ``min_words`` words above the confidence floor."""
    kept: list[Span] = []
    for q in quotable_spans:
        confident = sum(
            1
            for w in words
            if w.score is not None and w.score >= min_conf and q.start <= w.start <= q.end
        )
        if confident >= min_words:
            kept.append(q)
    return kept


def build_shortform_signals(data: SignalData, grid: Sequence[float]) -> dict[str, list[float]]:
    """Assemble the available shortform signal timelines on ``grid``."""
    signals: dict[str, list[float]] = {}

    if data.audio_times:
        signals["speech_intensity"] = normalized_curve(
            data.audio_times, data.speech_intensity, grid
        )
        signals["emotional_reactions"] = emotional_timeline(data.audio_times, data.energy, grid)
        signals["audio_peaks"] = influence_envelope(
            data.peak_times, grid, PEAK_INFLUENCE_RADIUS_SEC
        )

    if data.motion:
        signals["fast_motion"] = normalized_curve(data.motion_times, data.motion, grid)

    if data.has_transcript:
        spans = confident_word_spans(data.words, MIN_WORD_CONFIDENCE)
        hook_times = gate_hook_times(data.hook_times, spans)
        quotables = gate_quotable_spans(
            data.quotable_spans,
            data.words,
            min_conf=MIN_WORD_CONFIDENCE,
            min_words=MIN_QUOTABLE_WORDS,
        )
        signals["viral_hooks"] = influence_envelope(hook_times, grid, HOOK_INFLUENCE_RADIUS_SEC)
        signals["quotable_dialogue"] = coverage_mask([(q.start, q.end) for q in quotables], grid)

    return signals


def score_shortform(data: SignalData, configured_weights: Mapping[str, float]) -> ScoredTimeline:
    """Build the shortform composite timelines + effective (redistributed) weights."""
    grid = make_grid(data.duration_sec)
    signals = build_shortform_signals(data, grid)
    weights = redistribute_weights(configured_weights, set(signals))
    unavailable = sorted(set(configured_weights) - set(signals))
    return ScoredTimeline(
        grid=grid,
        timelines=signals,
        weights=weights,
        configured_weights=dict(configured_weights),
        unavailable=unavailable,
    )
