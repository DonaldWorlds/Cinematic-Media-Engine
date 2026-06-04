"""Phase 3 intelligence-layer tests (written before implementation).

Per SPEC §14 the scoring math is built and verified on synthetic data first,
then real signal outputs are plugged in. The pure functions (normalization,
grid resampling, weight redistribution, composite, emotional proxy, sliding
window, diversity clustering, scene snapping) are unit-tested with hand-built
inputs whose expected values can be computed by hand. The end-to-end
``run_intelligence`` is integration-tested against a synthetic workspace.
"""

from __future__ import annotations

import math
from pathlib import Path

# --------------------------------------------------------------------------- #
# scoring.normalize — per-run min-max to [0,1], flat -> 0.0
# --------------------------------------------------------------------------- #


def test_normalize_min_max_basic() -> None:
    from src.intelligence.scoring import normalize

    assert normalize([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]


def test_normalize_flat_signal_returns_zeros() -> None:
    from src.intelligence.scoring import normalize

    assert normalize([3.0, 3.0, 3.0]) == [0.0, 0.0, 0.0]


def test_normalize_empty_returns_empty() -> None:
    from src.intelligence.scoring import normalize

    assert normalize([]) == []


def test_normalize_single_value_is_flat_zero() -> None:
    from src.intelligence.scoring import normalize

    assert normalize([7.0]) == [0.0]


def test_normalize_stretches_already_bounded_signal() -> None:
    from src.intelligence.scoring import normalize

    out = normalize([0.2, 0.4, 0.8])
    assert out[0] == 0.0
    assert out[2] == 1.0
    assert math.isclose(out[1], (0.4 - 0.2) / (0.8 - 0.2))


def test_normalize_handles_negative_range() -> None:
    from src.intelligence.scoring import normalize

    assert normalize([-2.0, 0.0, 2.0]) == [0.0, 0.5, 1.0]


# --------------------------------------------------------------------------- #
# scoring.make_grid — per-second timeline anchor
# --------------------------------------------------------------------------- #


def test_make_grid_integer_duration_inclusive() -> None:
    from src.intelligence.scoring import make_grid

    assert make_grid(5.0, step_sec=1.0) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_make_grid_fractional_duration_floors_last_point() -> None:
    from src.intelligence.scoring import make_grid

    # matches librosa's per-second framing: 30.016s -> 31 points (0..30)
    grid = make_grid(30.016, step_sec=1.0)
    assert grid[0] == 0.0
    assert grid[-1] == 30.0
    assert len(grid) == 31


def test_make_grid_zero_duration_is_single_origin() -> None:
    from src.intelligence.scoring import make_grid

    assert make_grid(0.0, step_sec=1.0) == [0.0]


# --------------------------------------------------------------------------- #
# scoring.resample_curve — sampled curve -> common grid
# --------------------------------------------------------------------------- #


def test_resample_curve_linear_interpolation() -> None:
    from src.intelligence.scoring import resample_curve

    out = resample_curve([0.0, 2.0, 4.0], [0.0, 10.0, 20.0], [0.0, 1.0, 2.0, 3.0, 4.0])
    assert out == [0.0, 5.0, 10.0, 15.0, 20.0]


def test_resample_curve_empty_values_returns_zeros() -> None:
    from src.intelligence.scoring import resample_curve

    assert resample_curve([], [], [0.0, 1.0, 2.0]) == [0.0, 0.0, 0.0]


def test_resample_curve_single_point_is_constant() -> None:
    from src.intelligence.scoring import resample_curve

    assert resample_curve([1.0], [5.0], [0.0, 1.0, 2.0]) == [5.0, 5.0, 5.0]


# --------------------------------------------------------------------------- #
# scoring.redistribute_weights — drop unavailable, renormalize to 1.0
# --------------------------------------------------------------------------- #


def test_redistribute_weights_all_available_passthrough() -> None:
    from src.intelligence.scoring import redistribute_weights

    out = redistribute_weights({"a": 0.5, "b": 0.5}, {"a", "b"})
    assert out == {"a": 0.5, "b": 0.5}


def test_redistribute_weights_drops_and_renormalizes_proportionally() -> None:
    from src.intelligence.scoring import redistribute_weights

    out = redistribute_weights({"a": 0.3, "b": 0.2, "c": 0.5}, {"a", "b"})
    assert math.isclose(out["a"], 0.6)
    assert math.isclose(out["b"], 0.4)
    assert "c" not in out
    assert math.isclose(sum(out.values()), 1.0)


def test_redistribute_weights_none_available_returns_empty() -> None:
    from src.intelligence.scoring import redistribute_weights

    assert redistribute_weights({"a": 0.5, "b": 0.5}, set()) == {}


# --------------------------------------------------------------------------- #
# scoring.composite_timeline — per-grid-point weighted sum
# --------------------------------------------------------------------------- #


def test_composite_timeline_weighted_sum_per_point() -> None:
    from src.intelligence.scoring import composite_timeline

    out = composite_timeline({"a": [1.0, 0.0, 1.0], "b": [0.0, 1.0, 0.0]}, {"a": 0.5, "b": 0.5})
    assert out == [0.5, 0.5, 0.5]


def test_composite_timeline_ignores_signals_without_weights() -> None:
    from src.intelligence.scoring import composite_timeline

    out = composite_timeline(
        {"a": [1.0, 1.0], "b": [1.0, 1.0], "c": [9.0, 9.0]}, {"a": 0.5, "b": 0.5}
    )
    assert out == [1.0, 1.0]


def test_composite_timeline_empty_weights_returns_zeros() -> None:
    from src.intelligence.scoring import composite_timeline

    assert composite_timeline({"a": [1.0, 2.0, 3.0]}, {}) == [0.0, 0.0, 0.0]


def test_min_word_confidence_constant_is_documented_threshold() -> None:
    from src.intelligence.scoring import MIN_WORD_CONFIDENCE

    assert MIN_WORD_CONFIDENCE == 0.3


# --------------------------------------------------------------------------- #
# emotional_score.emotional_timeline — audio-dynamics proxy (|ΔRMS| envelope)
# --------------------------------------------------------------------------- #


def test_emotional_flat_energy_has_no_dynamics() -> None:
    from src.intelligence.emotional_score import emotional_timeline

    out = emotional_timeline([0.0, 1.0, 2.0, 3.0], [5.0, 5.0, 5.0, 5.0], [0.0, 1.0, 2.0, 3.0])
    assert out == [0.0, 0.0, 0.0, 0.0]


def test_emotional_peaks_at_energy_transition() -> None:
    from src.intelligence.emotional_score import emotional_timeline

    grid = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    out = emotional_timeline(grid, [0.0, 0.0, 0.0, 1.0, 1.0, 1.0], grid)
    assert out == [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def test_emotional_output_matches_grid_length() -> None:
    from src.intelligence.emotional_score import emotional_timeline

    grid = [0.0, 1.0, 2.0, 3.0, 4.0]
    out = emotional_timeline([0.0, 2.0, 4.0], [0.0, 3.0, 1.0], grid)
    assert len(out) == len(grid)
    assert all(0.0 <= v <= 1.0 for v in out)


def test_emotional_empty_energy_returns_zeros() -> None:
    from src.intelligence.emotional_score import emotional_timeline

    assert emotional_timeline([], [], [0.0, 1.0, 2.0]) == [0.0, 0.0, 0.0]


def test_emotional_single_grid_point_is_zero() -> None:
    from src.intelligence.emotional_score import emotional_timeline

    assert emotional_timeline([0.0], [9.0], [0.0]) == [0.0]


# --------------------------------------------------------------------------- #
# scoring signal primitives — curve / point-event / span builders
# --------------------------------------------------------------------------- #


def test_normalized_curve_resamples_then_normalizes() -> None:
    from src.intelligence.scoring import normalized_curve

    out = normalized_curve([0.0, 2.0, 4.0], [0.0, 10.0, 20.0], [0.0, 1.0, 2.0, 3.0, 4.0])
    assert out == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_normalized_curve_empty_returns_zeros() -> None:
    from src.intelligence.scoring import normalized_curve

    assert normalized_curve([], [], [0.0, 1.0, 2.0]) == [0.0, 0.0, 0.0]


def test_influence_envelope_triangular_bump_around_event() -> None:
    from src.intelligence.scoring import influence_envelope

    grid = [float(i) for i in range(11)]
    out = influence_envelope([5.0], grid, radius_sec=2.0)
    assert out[5] == 1.0
    assert out[4] == 0.5
    assert out[6] == 0.5
    assert out[3] == 0.0
    assert out[7] == 0.0
    assert out[0] == 0.0


def test_influence_envelope_no_events_is_zeros() -> None:
    from src.intelligence.scoring import influence_envelope

    assert influence_envelope([], [0.0, 1.0, 2.0], radius_sec=2.0) == [0.0, 0.0, 0.0]


def test_influence_envelope_takes_max_over_events() -> None:
    from src.intelligence.scoring import influence_envelope

    grid = [float(i) for i in range(11)]
    out = influence_envelope([2.0, 8.0], grid, radius_sec=2.0)
    assert out[2] == 1.0
    assert out[8] == 1.0
    assert out[5] == 0.0


def test_coverage_mask_marks_points_inside_spans() -> None:
    from src.intelligence.scoring import coverage_mask

    out = coverage_mask([(2.0, 4.0)], [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert out == [0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0]


def test_coverage_mask_empty_spans_is_zeros() -> None:
    from src.intelligence.scoring import coverage_mask

    assert coverage_mask([], [0.0, 1.0, 2.0]) == [0.0, 0.0, 0.0]


# --------------------------------------------------------------------------- #
# viral_score — word-confidence gating of transcript-derived signals
# --------------------------------------------------------------------------- #


def _words(*specs: tuple[float, float, float | None]):
    from src.intelligence.scoring import WordSpan

    return [WordSpan(start=s, end=e, score=sc) for s, e, sc in specs]


def test_confident_word_spans_filters_below_threshold() -> None:
    from src.intelligence.scoring import Span
    from src.intelligence.viral_score import confident_word_spans

    words = _words((0.0, 1.0, 0.066), (1.0, 2.0, 0.9), (2.0, 3.0, None))
    out = confident_word_spans(words, 0.3)
    assert out == [Span(start=1.0, end=2.0)]


def test_gate_hook_times_keeps_only_hooks_on_confident_words() -> None:
    from src.intelligence.viral_score import confident_word_spans, gate_hook_times

    words = _words((0.0, 1.0, 0.066), (1.0, 2.0, 0.9))
    spans = confident_word_spans(words, 0.3)
    # 1.5 lands on the confident word (1..2); 0.5 lands on the low-confidence word (0..1)
    assert gate_hook_times([1.5, 0.5], spans) == [1.5]


def test_gate_quotable_keeps_when_enough_confident_words_remain() -> None:
    from src.intelligence.scoring import Span
    from src.intelligence.viral_score import gate_quotable_spans

    # leading word low-confidence (like the fixture's "again," @0.066), 5 confident after
    words = _words(
        (0.0, 1.0, 0.066),
        (1.0, 2.0, 0.9),
        (2.0, 3.0, 0.9),
        (3.0, 4.0, 0.9),
        (4.0, 5.0, 0.9),
        (5.0, 6.0, 0.9),
    )
    kept = gate_quotable_spans([Span(start=0.0, end=6.0)], words, min_conf=0.3, min_words=5)
    assert kept == [Span(start=0.0, end=6.0)]


def test_gate_quotable_drops_when_too_few_confident_words() -> None:
    from src.intelligence.scoring import Span
    from src.intelligence.viral_score import gate_quotable_spans

    words = _words((0.0, 1.0, 0.066), (1.0, 2.0, 0.1))  # both below threshold
    kept = gate_quotable_spans([Span(start=0.0, end=2.0)], words, min_conf=0.3, min_words=5)
    assert kept == []


# --------------------------------------------------------------------------- #
# viral_score.score_shortform — assembly + weight redistribution
# --------------------------------------------------------------------------- #


def _full_shortform_data():
    from src.intelligence.scoring import SignalData, Span, WordSpan

    times = [float(i) for i in range(11)]
    return SignalData(
        duration_sec=10.0,
        audio_times=times,
        energy=[0.1 * i for i in range(11)],
        speech_intensity=[0.5] * 11,
        peak_times=[5.0],
        motion_times=times,
        motion=[0.2 * (i % 3) for i in range(11)],
        aesthetics_times=times,
        aesthetics={"lighting_quality": [0.8] * 11, "composition_score": [0.4] * 11},
        has_transcript=True,
        words=[WordSpan(start=1.0, end=2.0, score=0.9)] * 6,
        hook_times=[1.5],
        quotable_spans=[Span(start=1.0, end=6.0)],
        scene_cuts_sec=[],
    )


def test_score_shortform_all_signals_present_no_redistribution() -> None:
    from src.config import load_mode_config
    from src.intelligence.viral_score import score_shortform

    weights = load_mode_config("shortform").scoring_weights
    scored = score_shortform(_full_shortform_data(), weights)

    assert set(scored.timelines) == set(weights)
    assert scored.unavailable == []
    assert scored.weights == weights
    assert all(len(tl) == len(scored.grid) for tl in scored.timelines.values())


def test_score_shortform_redistributes_when_motion_missing() -> None:
    from src.config import load_mode_config
    from src.intelligence.viral_score import score_shortform

    data = _full_shortform_data()
    data.motion = []
    data.motion_times = []
    weights = load_mode_config("shortform").scoring_weights
    scored = score_shortform(data, weights)

    assert "fast_motion" not in scored.timelines
    assert "fast_motion" in scored.unavailable
    assert math.isclose(sum(scored.weights.values()), 1.0)
    assert "fast_motion" not in scored.weights


def test_score_shortform_hooks_present_but_empty_stay_available() -> None:
    from src.config import load_mode_config
    from src.intelligence.viral_score import score_shortform

    data = _full_shortform_data()
    data.hook_times = []  # transcript exists but zero hooks found
    weights = load_mode_config("shortform").scoring_weights
    scored = score_shortform(data, weights)

    assert "viral_hooks" in scored.timelines
    assert set(scored.timelines["viral_hooks"]) == {0.0}
    assert "viral_hooks" not in scored.unavailable


# --------------------------------------------------------------------------- #
# cinematic_score — pacing-curve proxy + assembly with redistribution
# --------------------------------------------------------------------------- #


def test_blend_pacing_averages_curves_elementwise() -> None:
    from src.intelligence.cinematic_score import blend_pacing

    assert blend_pacing([[0.0, 1.0, 0.0], [1.0, 0.0, 1.0]]) == [0.5, 0.5, 0.5]


def test_blend_pacing_single_curve_passthrough() -> None:
    from src.intelligence.cinematic_score import blend_pacing

    assert blend_pacing([[0.2, 0.8]]) == [0.2, 0.8]


def _cinematic_data():
    from src.intelligence.scoring import SignalData

    times = [float(i) for i in range(11)]
    return SignalData(
        duration_sec=10.0,
        audio_times=times,
        energy=[0.1 * i for i in range(11)],
        speech_intensity=[0.5] * 11,
        motion_times=times,
        motion=[0.2 * (i % 3) for i in range(11)],
        aesthetics_times=times,
        aesthetics={"lighting_quality": [0.8] * 11, "composition_score": [0.4] * 11},
    )


def test_score_cinematic_marks_openclip_and_soundtrack_unavailable() -> None:
    from src.config import load_mode_config
    from src.intelligence.cinematic_score import score_cinematic

    weights = load_mode_config("cinematic_longform").scoring_weights
    scored = score_cinematic(_cinematic_data(), weights)

    assert set(scored.timelines) == {
        "emotional_moments",
        "lighting_quality",
        "visual_composition",
        "pacing_curve",
    }
    assert scored.unavailable == ["openclip_aesthetic", "soundtrack_sync"]
    assert math.isclose(sum(scored.weights.values()), 1.0)
    # available configured weights sum to 0.80 -> emotional_moments 0.25 renormalizes to 0.25/0.80
    assert math.isclose(scored.weights["emotional_moments"], 0.25 / 0.80)


# --------------------------------------------------------------------------- #
# Phase 8 — cinematic_longform routes to the cinematic scorer (distinct from
# score_standard) and ships a color_grade block. Deep redistribution math is
# covered above; these guard the wiring.
# --------------------------------------------------------------------------- #


def test_cinematic_longform_mode_routes_to_cinematic_scorer() -> None:
    from src.intelligence import _SCORERS
    from src.intelligence.cinematic_score import score_cinematic
    from src.intelligence.scoring import score_standard

    assert _SCORERS["cinematic_longform"] is score_cinematic
    assert _SCORERS["cinematic_longform"] is not score_standard  # NOT the standard scorer


def test_cinematic_longform_yaml_has_color_grade_block() -> None:
    from src.config import load_mode_config

    cg = load_mode_config("cinematic_longform").model_dump()["color_grade"]
    assert cg["enabled"] is True
    assert "lut_path" in cg  # required when grading is enabled


def test_score_cinematic_drops_aesthetic_keys_when_absent() -> None:
    from src.config import load_mode_config
    from src.intelligence.cinematic_score import score_cinematic

    data = _cinematic_data()
    data.aesthetics = {}
    weights = load_mode_config("cinematic_longform").scoring_weights
    scored = score_cinematic(data, weights)

    assert "lighting_quality" not in scored.timelines
    assert "visual_composition" not in scored.timelines
    assert "lighting_quality" in scored.unavailable


# --------------------------------------------------------------------------- #
# pacing_engine.build_pacing_plan — mode pacing block -> window/diversity plan
# --------------------------------------------------------------------------- #


def test_pacing_plan_shortform_locked_60s_window() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan

    plan = build_pacing_plan(
        {"min_segment_sec": 60, "max_segment_sec": 60, "preferred_duration_sec": 60},
        diversity={"min_gap_sec": 30, "max_clips_per_minute": 2},
        score_threshold=0.55,
        target_clips=20,
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    assert plan.window_sec == 60.0
    assert plan.step_sec == 1.0
    assert plan.min_gap_sec == 30.0
    assert plan.max_clips_per_minute == 2.0
    assert plan.score_threshold == 0.55
    assert plan.max_clips == 20


def test_pacing_plan_falls_back_to_max_segment_and_defaults() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan

    plan = build_pacing_plan(
        {"min_segment_sec": 8, "max_segment_sec": 60},  # no preferred, no diversity/threshold
        diversity=None,
        score_threshold=None,
        target_clips=None,
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    assert plan.window_sec == 60.0  # falls back to max_segment_sec
    assert plan.min_gap_sec == 15.0  # global default
    assert plan.score_threshold == 0.4  # global default
    assert plan.max_clips_per_minute is None
    assert plan.max_clips is None


# --------------------------------------------------------------------------- #
# clustering.slide_windows — sliding 60s window, mean composite
# --------------------------------------------------------------------------- #


def _scored(grid, timelines, weights):
    from src.intelligence.scoring import ScoredTimeline

    return ScoredTimeline(
        grid=grid, timelines=timelines, weights=weights, configured_weights=weights, unavailable=[]
    )


def test_slide_windows_short_source_single_clamped_window() -> None:
    from src.intelligence.clustering import slide_windows

    scored = _scored(
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], {"a": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]}, {"a": 1.0}
    )
    out = slide_windows(scored, window_sec=10.0, step_sec=1.0, duration_sec=5.0)
    assert len(out) == 1
    assert out[0].start_sec == 0.0
    assert out[0].end_sec == 5.0
    assert math.isclose(out[0].score, 0.5)
    assert math.isclose(out[0].contributions["a"], 0.5)


def test_slide_windows_multiple_windows_mean_composite() -> None:
    from src.intelligence.clustering import slide_windows

    grid = [float(i) for i in range(11)]
    timelines = {"a": [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0]}
    scored = _scored(grid, timelines, {"a": 1.0})
    out = slide_windows(scored, window_sec=4.0, step_sec=2.0, duration_sec=10.0)
    assert [c.start_sec for c in out] == [0.0, 2.0, 4.0, 6.0]
    by_start = {c.start_sec: c for c in out}
    assert math.isclose(by_start[4.0].score, 0.8)  # mean of [0,1,1,1,1]


# --------------------------------------------------------------------------- #
# clustering.select_diverse — greedy min-gap + per-minute cap
# --------------------------------------------------------------------------- #


def _cand(start, score):
    from src.intelligence.clustering import WindowCandidate

    return WindowCandidate(start_sec=start, end_sec=start + 60.0, score=score, contributions={})


def test_select_diverse_enforces_min_gap() -> None:
    from src.intelligence.clustering import select_diverse

    cands = [_cand(0.0, 0.9), _cand(10.0, 0.8), _cand(40.0, 0.7)]
    out = select_diverse(cands, min_gap_sec=30.0, max_clips_per_minute=None)
    assert [c.start_sec for c in out] == [0.0, 40.0]


def test_select_diverse_respects_max_clips_per_minute() -> None:
    from src.intelligence.clustering import select_diverse

    cands = [_cand(0.0, 0.9), _cand(20.0, 0.8), _cand(40.0, 0.7)]
    out = select_diverse(cands, min_gap_sec=15.0, max_clips_per_minute=2.0)
    assert [c.start_sec for c in out] == [0.0, 20.0]


# --------------------------------------------------------------------------- #
# ranking — scene snapping, threshold, cap, ranked moments
# --------------------------------------------------------------------------- #


def test_snap_boundary_snaps_to_nearest_cut_within_tolerance() -> None:
    from src.intelligence.ranking import snap_boundary

    value, snapped = snap_boundary(10.3, [4.0, 10.0, 25.0], tolerance_sec=2.0)
    assert value == 10.0
    assert snapped is True


def test_snap_boundary_no_cuts_is_noop() -> None:
    from src.intelligence.ranking import snap_boundary

    value, snapped = snap_boundary(10.3, [], tolerance_sec=2.0)
    assert value == 10.3
    assert snapped is False


def test_snap_boundary_beyond_tolerance_is_noop() -> None:
    from src.intelligence.ranking import snap_boundary

    value, snapped = snap_boundary(10.3, [4.0, 25.0], tolerance_sec=2.0)
    assert value == 10.3
    assert snapped is False


def test_rank_moments_filters_threshold_and_ranks_by_score() -> None:
    from src.intelligence.clustering import WindowCandidate
    from src.intelligence.ranking import rank_moments

    cands = [
        WindowCandidate(start_sec=0.0, end_sec=60.0, score=0.4, contributions={"a": 0.4}),
        WindowCandidate(start_sec=90.0, end_sec=150.0, score=0.8, contributions={"a": 0.8}),
        WindowCandidate(start_sec=200.0, end_sec=260.0, score=0.6, contributions={"a": 0.6}),
    ]
    moments = rank_moments(
        cands, score_threshold=0.55, max_clips=None, scene_cuts=[], snap_tolerance_sec=2.0
    )
    assert [m.rank for m in moments] == [1, 2]
    assert [m.composite_score for m in moments] == [0.8, 0.6]  # 0.4 dropped


def test_rank_moments_empty_when_all_below_threshold() -> None:
    from src.intelligence.clustering import WindowCandidate
    from src.intelligence.ranking import rank_moments

    cands = [WindowCandidate(start_sec=0.0, end_sec=30.0, score=0.4, contributions={"a": 0.4})]
    moments = rank_moments(
        cands, score_threshold=0.55, max_clips=None, scene_cuts=[], snap_tolerance_sec=2.0
    )
    assert moments == []


def test_rank_moments_caps_at_max_clips() -> None:
    from src.intelligence.clustering import WindowCandidate
    from src.intelligence.ranking import rank_moments

    cands = [
        WindowCandidate(
            start_sec=float(i * 90),
            end_sec=float(i * 90 + 60),
            score=0.6 + i * 0.05,
            contributions={},
        )
        for i in range(5)
    ]
    moments = rank_moments(
        cands, score_threshold=0.5, max_clips=2, scene_cuts=[], snap_tolerance_sec=2.0
    )
    assert len(moments) == 2


def _best_window(score: float = 0.7):
    from src.intelligence.ranking import BestWindow

    return BestWindow(
        start_sec=0.0,
        end_sec=60.0,
        composite_score=score,
        signal_contributions={"speech_intensity": score},
        signal_values={"speech_intensity": score},
    )


def test_build_moments_result_reports_weights_contributions_and_best_window() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan
    from src.intelligence.ranking import Moment, build_moments_result
    from src.intelligence.scoring import ScoredTimeline

    scored = ScoredTimeline(
        grid=[0.0, 1.0],
        timelines={"speech_intensity": [0.6, 0.8]},
        weights={"speech_intensity": 1.0},
        configured_weights={"speech_intensity": 0.25, "fast_motion": 0.75},
        unavailable=["fast_motion"],
    )
    plan = build_pacing_plan(
        {"preferred_duration_sec": 60, "max_segment_sec": 60},
        diversity={"min_gap_sec": 30, "max_clips_per_minute": 2},
        score_threshold=0.55,
        target_clips=20,
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    moment = Moment(
        rank=1,
        start_sec=0.0,
        end_sec=60.0,
        duration_sec=60.0,
        composite_score=0.7,
        scene_snapped=False,
        signal_contributions={"speech_intensity": 0.7},
    )
    result = build_moments_result(
        run_id="run_test",
        mode="shortform",
        source="x.mp4",
        generated_at="2026-05-26T00:00:00",
        source_duration_sec=120.0,
        composite_method="weighted_sum",
        scored=scored,
        plan=plan,
        best_window=_best_window(0.7),
        moments=[moment],
        below_threshold_best_candidates=[],
        used_fallback=False,
    )
    assert result.moment_count == 1
    assert result.weights.unavailable_signals == ["fast_motion"]
    assert result.weights.configured == {"speech_intensity": 0.25, "fast_motion": 0.75}
    assert result.moments[0].signal_contributions == {"speech_intensity": 0.7}
    assert result.diversity.min_gap_sec == 30.0
    assert result.best_window.composite_score == 0.7
    assert result.best_window.signal_values == {"speech_intensity": 0.7}
    assert result.used_fallback is False
    assert result.below_threshold_best_candidates == []


def test_build_moments_result_reports_fallback_candidates_when_empty() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan
    from src.intelligence.ranking import Moment, build_moments_result
    from src.intelligence.scoring import ScoredTimeline

    scored = ScoredTimeline(
        grid=[0.0],
        timelines={"speech_intensity": [0.3]},
        weights={"speech_intensity": 1.0},
        configured_weights={"speech_intensity": 1.0},
        unavailable=[],
    )
    plan = build_pacing_plan(
        {"preferred_duration_sec": 60, "max_segment_sec": 60},
        score_threshold=0.55,
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    fallback = [
        Moment(
            rank=1,
            start_sec=0.0,
            end_sec=30.0,
            duration_sec=30.0,
            composite_score=0.42,
            scene_snapped=False,
            signal_contributions={"speech_intensity": 0.42},
        )
    ]
    result = build_moments_result(
        run_id="r",
        mode="shortform",
        source="x.mp4",
        generated_at="t",
        source_duration_sec=30.0,
        composite_method="weighted_sum",
        scored=scored,
        plan=plan,
        best_window=_best_window(0.42),
        moments=[],
        below_threshold_best_candidates=fallback,
        used_fallback=True,
    )
    assert result.moment_count == 0
    assert result.used_fallback is True
    assert len(result.below_threshold_best_candidates) == 1
    assert result.best_window.composite_score == 0.42


# --------------------------------------------------------------------------- #
# scoring.score_standard — generic standard_longform path (no bespoke module)
# --------------------------------------------------------------------------- #


def test_score_standard_marks_semantic_signals_unavailable() -> None:
    from src.config import load_mode_config
    from src.intelligence.scoring import score_standard

    weights = load_mode_config("standard_longform").scoring_weights
    scored = score_standard(_full_shortform_data(), weights)

    assert "audio_energy" in scored.timelines
    assert "conversational" in scored.timelines
    assert "transcript_quality" in scored.timelines
    assert "narrative_continuity" in scored.unavailable
    assert "pacing_consistency" in scored.unavailable
    assert math.isclose(sum(scored.weights.values()), 1.0)


def test_score_standard_without_transcript_drops_transcript_quality() -> None:
    from src.config import load_mode_config
    from src.intelligence.scoring import score_standard

    data = _full_shortform_data()
    data.has_transcript = False
    data.words = []
    weights = load_mode_config("standard_longform").scoring_weights
    scored = score_standard(data, weights)

    assert "transcript_quality" in scored.unavailable
    assert "audio_energy" in scored.timelines


# --------------------------------------------------------------------------- #
# run_intelligence — end-to-end on a synthetic workspace -> moments.json
# --------------------------------------------------------------------------- #


def _write_synthetic_workspace(tmp_path: Path):
    """A 120s synthetic workspace whose [55,120] region is strong on every signal."""
    from src.analysis import AnalysisOutputs
    from src.ingestion.workspace import create_workspace
    from src.utils.fileio import write_json

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_synth")
    write_json(ws.metadata_path, {"path": str(source), "duration_sec": 120.0})

    grid = [float(i) for i in range(121)]
    energy = [0.0 if i < 55 else (i - 55) / 65.0 for i in range(121)]
    hot = [0.0 if i < 55 else 1.0 for i in range(121)]
    write_json(
        ws.analysis_dir / "librosa.json",
        {
            "sample_rate": 16000,
            "duration_sec": 120.0,
            "window_sec": 1.0,
            "times_sec": grid,
            "energy": energy,
            "speech_intensity": hot,
            "peaks": [
                {"time_sec": float(t), "rms": 1.0, "z_score": 3.0} for t in range(57, 120, 3)
            ],
            "mean_rms": 0.5,
            "max_rms": 1.0,
        },
    )
    write_json(
        ws.analysis_dir / "opencv.json",
        {
            "duration_sec": 120.0,
            "sample_rate_hz": 2.0,
            "motion_times_sec": grid,
            "motion": hot,
            "mean_motion": 0.5,
            "max_motion": 1.0,
            "scene_cuts_sec": [],
            "scene_count": 1,
        },
    )
    write_json(
        ws.analysis_dir / "aesthetics.json",
        {
            "duration_sec": 120.0,
            "sample_rate_hz": 1.0,
            "times_sec": grid,
            "metrics": {"lighting_quality": [0.8] * 121, "composition_score": [0.4] * 121},
            "means": {"lighting_quality": 0.8, "composition_score": 0.4},
            "frame_count": 121,
        },
    )
    words = [{"word": "w", "start": float(i), "end": i + 0.9, "score": 0.9} for i in range(55, 120)]
    write_json(
        ws.transcript_path,
        {
            "language": "en",
            "text": "w " * 65,
            "segments": [{"start": 55.0, "end": 120.0, "text": "w " * 65, "words": words}],
            "word_count": 65,
            "aligned": True,
        },
    )
    write_json(
        ws.analysis_dir / "hooks.json",
        {
            "hooks": [
                {"phrase": "yo", "time_sec": float(t), "segment_index": 0, "within_window": True}
                for t in (57, 60, 63)
            ],
            "quotables": [
                {"text": "w " * 60, "start_sec": 55.0, "end_sec": 118.0, "word_count": 60}
            ],
            "hook_count": 3,
            "quotable_count": 1,
        },
    )
    outputs = AnalysisOutputs(
        librosa_path=ws.analysis_dir / "librosa.json",
        opencv_path=ws.analysis_dir / "opencv.json",
        aesthetics_path=ws.analysis_dir / "aesthetics.json",
        transcript_path=ws.transcript_path,
        hooks_path=ws.analysis_dir / "hooks.json",
        transcribed=True,
    )
    return source, ws, outputs


def test_run_intelligence_writes_valid_moments_json(tmp_path: Path) -> None:
    from src.config import load_config, load_mode_config
    from src.intelligence import run_intelligence
    from src.intelligence.ranking import MomentsResult
    from src.utils.fileio import read_json

    source, ws, outputs = _write_synthetic_workspace(tmp_path)
    config = load_config()
    mode = load_mode_config("shortform")

    result = run_intelligence(source, ws, config, mode, outputs)

    assert ws.moments_path.exists()
    on_disk = MomentsResult.model_validate(read_json(ws.moments_path))
    assert on_disk.mode == "shortform"
    assert on_disk.window_sec == 60.0
    assert on_disk.composite_method == "weighted_sum"
    assert on_disk.weights.unavailable_signals == []  # all 6 shortform signals present
    assert on_disk.moment_count == len(on_disk.moments)
    assert result.moment_count == on_disk.moment_count
    # best_window is always present (replaces the old scalar best_window_score)
    assert set(on_disk.best_window.signal_contributions) == set(on_disk.weights.effective)
    assert set(on_disk.best_window.signal_values) == set(on_disk.weights.effective)
    assert isinstance(on_disk.used_fallback, bool)


def test_run_intelligence_produces_ranked_moment_for_strong_region(tmp_path: Path) -> None:
    from src.config import load_config, load_mode_config
    from src.intelligence import run_intelligence

    source, ws, outputs = _write_synthetic_workspace(tmp_path)
    result = run_intelligence(source, ws, load_config(), load_mode_config("shortform"), outputs)

    assert result.moment_count >= 1
    top = result.moments[0]
    assert top.rank == 1
    assert top.composite_score >= 0.55  # mode score_threshold
    assert math.isclose(top.duration_sec, 60.0, abs_tol=1.0)
    assert top.scene_snapped is False  # synthetic opencv has no scene cuts
    assert math.isclose(sum(top.signal_contributions.values()), top.composite_score, abs_tol=1e-9)
    assert result.used_fallback is False
    assert result.below_threshold_best_candidates == []
    assert result.best_window.composite_score >= 0.55


# --------------------------------------------------------------------------- #
# Step 1 / Fix B — signal_values, top_n_fallback, select_moments, fallback wiring
# --------------------------------------------------------------------------- #


def test_slide_windows_records_raw_signal_values() -> None:
    from src.intelligence.clustering import slide_windows

    scored = _scored(
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], {"a": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]}, {"a": 0.5}
    )
    out = slide_windows(scored, window_sec=10.0, step_sec=1.0, duration_sec=5.0)
    assert math.isclose(out[0].signal_values["a"], 0.5)  # raw windowed mean, pre-weight
    assert math.isclose(out[0].contributions["a"], 0.25)  # weight 0.5 * mean 0.5
    assert math.isclose(out[0].score, 0.25)


def test_pacing_plan_top_n_fallback_defaults_to_5() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan

    plan = build_pacing_plan(
        {"preferred_duration_sec": 60, "max_segment_sec": 60},
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    assert plan.top_n_fallback == 5


def test_pacing_plan_top_n_fallback_explicit() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan

    plan = build_pacing_plan(
        {"preferred_duration_sec": 60, "max_segment_sec": 60},
        top_n_fallback=3,
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    assert plan.top_n_fallback == 3


def test_select_moments_returns_threshold_passers() -> None:
    from src.intelligence.clustering import WindowCandidate
    from src.intelligence.ranking import select_moments

    cands = [
        WindowCandidate(start_sec=0.0, end_sec=60.0, score=0.8, contributions={"a": 0.8}),
        WindowCandidate(start_sec=90.0, end_sec=150.0, score=0.3, contributions={"a": 0.3}),
    ]
    moments, fallback, used = select_moments(
        cands,
        score_threshold=0.55,
        max_clips=None,
        top_n_fallback=5,
        scene_cuts=[],
        snap_tolerance_sec=2.0,
    )
    assert [m.composite_score for m in moments] == [0.8]
    assert fallback == []
    assert used is False


def test_select_moments_falls_back_to_top_n_when_none_clear() -> None:
    from src.intelligence.clustering import WindowCandidate
    from src.intelligence.ranking import select_moments

    cands = [
        WindowCandidate(
            start_sec=float(i * 90),
            end_sec=float(i * 90 + 60),
            score=0.3 + i * 0.02,
            contributions={},
        )
        for i in range(8)
    ]
    moments, fallback, used = select_moments(
        cands,
        score_threshold=0.55,
        max_clips=None,
        top_n_fallback=5,
        scene_cuts=[],
        snap_tolerance_sec=2.0,
    )
    assert moments == []
    assert used is True
    assert len(fallback) == 5  # capped at top_n_fallback
    assert fallback[0].rank == 1
    assert fallback[0].composite_score == max(c.score for c in cands)  # ranked desc


def _write_low_signal_workspace(tmp_path: Path):
    """A 90s workspace whose best window stays below the 0.55 threshold (forces fallback)."""
    from src.analysis import AnalysisOutputs
    from src.ingestion.workspace import create_workspace
    from src.utils.fileio import write_json

    source = tmp_path / "weak.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_weak")
    write_json(ws.metadata_path, {"path": str(source), "duration_sec": 90.0})
    grid = [float(i) for i in range(91)]
    write_json(
        ws.analysis_dir / "librosa.json",
        {
            "sample_rate": 16000,
            "duration_sec": 90.0,
            "window_sec": 1.0,
            "times_sec": grid,
            "energy": [0.1 + 0.0005 * i for i in range(91)],
            "speech_intensity": [0.1 + 0.0005 * i for i in range(91)],
            "peaks": [],
            "mean_rms": 0.1,
            "max_rms": 0.15,
        },
    )
    write_json(
        ws.analysis_dir / "opencv.json",
        {
            "duration_sec": 90.0,
            "sample_rate_hz": 2.0,
            "motion_times_sec": grid,
            "motion": [0.05] * 91,
            "mean_motion": 0.05,
            "max_motion": 0.05,
            "scene_cuts_sec": [],
            "scene_count": 1,
        },
    )
    outputs = AnalysisOutputs(
        librosa_path=ws.analysis_dir / "librosa.json",
        opencv_path=ws.analysis_dir / "opencv.json",
        transcribed=False,
    )
    return source, ws, outputs


def test_run_intelligence_uses_fallback_when_nothing_clears_threshold(tmp_path: Path) -> None:
    from src.config import load_config, load_mode_config
    from src.intelligence import run_intelligence

    source, ws, outputs = _write_low_signal_workspace(tmp_path)
    result = run_intelligence(source, ws, load_config(), load_mode_config("shortform"), outputs)

    assert result.moment_count == 0
    assert result.used_fallback is True
    assert 1 <= len(result.below_threshold_best_candidates) <= 5
    assert result.below_threshold_best_candidates[0].rank == 1
    assert result.best_window.composite_score < 0.55


# --------------------------------------------------------------------------- #
# Phase 3.5 — aggregate_window (mean | percentile)
# --------------------------------------------------------------------------- #


def test_aggregate_window_defaults_to_mean() -> None:
    from src.intelligence.scoring import aggregate_window

    assert aggregate_window([0.0, 0.5, 1.0]) == 0.5


def test_aggregate_window_percentile_90() -> None:
    from src.intelligence.scoring import aggregate_window

    assert math.isclose(
        aggregate_window([float(i) for i in range(11)], method="percentile", percentile=90.0), 9.0
    )


def test_aggregate_window_percentile_extremes_are_min_median_max() -> None:
    from src.intelligence.scoring import aggregate_window

    vals = [0.0, 0.25, 0.5, 0.75, 1.0]
    assert aggregate_window(vals, method="percentile", percentile=0.0) == 0.0
    assert aggregate_window(vals, method="percentile", percentile=50.0) == 0.5
    assert aggregate_window(vals, method="percentile", percentile=100.0) == 1.0


def test_aggregate_window_percentile_beats_mean_on_peaky_window() -> None:
    from src.intelligence.scoring import aggregate_window

    vals = [0.0] * 5 + [1.0] * 5  # half the window is hot
    assert aggregate_window(vals) == 0.5  # mean dilutes
    assert aggregate_window(vals, method="percentile", percentile=90.0) == 1.0  # peak survives


def test_aggregate_window_empty_is_zero() -> None:
    from src.intelligence.scoring import aggregate_window

    assert aggregate_window([]) == 0.0
    assert aggregate_window([], method="percentile", percentile=90.0) == 0.0


def test_slide_windows_percentile_beats_mean_and_default_is_mean() -> None:
    from src.intelligence.clustering import slide_windows

    scored = _scored(
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], {"a": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]}, {"a": 1.0}
    )
    out_mean = slide_windows(scored, window_sec=10.0, step_sec=1.0, duration_sec=5.0)
    out_pct = slide_windows(
        scored,
        window_sec=10.0,
        step_sec=1.0,
        duration_sec=5.0,
        method="percentile",
        percentile=90.0,
    )
    assert math.isclose(out_mean[0].score, 0.5)  # default mean dilutes the hot half
    assert math.isclose(out_pct[0].score, 1.0)  # p90 captures it


def test_pacing_plan_carries_window_aggregation() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan

    plan = build_pacing_plan(
        {"preferred_duration_sec": 60, "max_segment_sec": 60},
        window_aggregation="percentile",
        window_percentile=90.0,
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    assert plan.window_aggregation == "percentile"
    assert plan.window_percentile == 90.0


def test_pacing_plan_window_aggregation_defaults_to_mean() -> None:
    from src.intelligence.pacing_engine import build_pacing_plan

    plan = build_pacing_plan(
        {"preferred_duration_sec": 60, "max_segment_sec": 60},
        default_min_gap_sec=15.0,
        default_threshold=0.4,
    )
    assert plan.window_aggregation == "mean"
    assert plan.window_percentile == 90.0


def test_config_scoring_window_aggregation_is_percentile_90() -> None:
    from src.config import load_config

    scoring = load_config().scoring
    assert scoring.window_aggregation == "percentile"  # production default (Phase 3.5)
    assert scoring.window_percentile == 90.0


# --------------------------------------------------------------------------- #
# Phase 7 — thumbnail text suggestions (intelligence/thumbnail_text.py).
# Short (3-7 word) quotable phrases from the transcript within the cut window,
# with an explicit status so Descript/automation can tell "no transcript" from
# "no quotable content".
# --------------------------------------------------------------------------- #


def test_thumbnail_text_no_transcript_returns_status() -> None:
    from src.intelligence.thumbnail_text import extract_thumbnail_text

    result = extract_thumbnail_text(
        transcript_available=False,
        words=[],
        quotables=[],
        window_start=0.0,
        window_end=300.0,
        count=3,
    )
    assert result.suggestions == []
    assert result.status == "transcript_unavailable"


def test_thumbnail_text_ok_returns_short_phrases() -> None:
    from src.intelligence.thumbnail_text import extract_thumbnail_text

    words = [
        ("this", 70.0, 70.3, 0.9),
        ("is", 70.3, 70.5, 0.9),
        ("absolutely", 70.5, 71.0, 0.9),
        ("insane", 71.0, 71.5, 0.9),
        ("right", 71.5, 71.8, 0.9),
    ]
    result = extract_thumbnail_text(
        transcript_available=True,
        words=words,
        quotables=[(70.0, 71.8)],
        window_start=60.0,
        window_end=300.0,
        count=3,
    )
    assert result.status == "ok"
    assert result.suggestions
    for phrase in result.suggestions:
        assert 3 <= len(phrase.split()) <= 7  # short, thumbnail-friendly


def test_thumbnail_text_excludes_words_outside_window() -> None:
    from src.intelligence.thumbnail_text import extract_thumbnail_text

    words = [
        ("way", 10.0, 10.3, 0.9),
        ("before", 10.3, 10.6, 0.9),
        ("window", 10.6, 11.0, 0.9),
        ("inside", 100.0, 100.3, 0.9),
        ("the", 100.3, 100.5, 0.9),
        ("window", 100.5, 101.0, 0.9),
    ]
    result = extract_thumbnail_text(
        transcript_available=True,
        words=words,
        quotables=[(10.0, 11.0), (100.0, 101.0)],
        window_start=60.0,
        window_end=300.0,
        count=3,
    )
    joined = " ".join(result.suggestions).lower()
    assert "before" not in joined  # quotable entirely outside the cut window is dropped
    assert "inside" in joined


def test_thumbnail_text_ranked_by_confidence() -> None:
    from src.intelligence.thumbnail_text import extract_thumbnail_text

    words = [
        ("weak", 70.0, 70.3, 0.31),
        ("low", 70.3, 70.6, 0.32),
        ("score", 70.6, 71.0, 0.33),
        ("strong", 100.0, 100.3, 0.99),
        ("high", 100.3, 100.6, 0.98),
        ("score", 100.6, 101.0, 0.97),
    ]
    result = extract_thumbnail_text(
        transcript_available=True,
        words=words,
        quotables=[(70.0, 71.0), (100.0, 101.0)],
        window_start=60.0,
        window_end=300.0,
        count=2,
    )
    assert result.status == "ok"
    assert result.suggestions[0].split()[0] == "strong"  # highest-confidence phrase ranks first


def test_thumbnail_text_no_quotables_returns_status() -> None:
    from src.intelligence.thumbnail_text import extract_thumbnail_text

    result = extract_thumbnail_text(
        transcript_available=True,
        words=[("hi", 100.0, 100.2, 0.9)],
        quotables=[],  # transcript present but nothing quotable in the window
        window_start=60.0,
        window_end=300.0,
        count=3,
    )
    assert result.suggestions == []
    assert result.status == "no_quotable_phrases_found"


# --------------------------------------------------------------------------- #
# Phase 7 — moments.json thumbnail fields (additive, default empty; mirrors the
# ReframeDecision additive pattern — must not perturb shortform consumers).
# --------------------------------------------------------------------------- #


def _bare_moments_result():
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        MomentsResult,
        WeightsReport,
    )

    return MomentsResult(
        run_id="r",
        mode="shortform",
        source="s",
        generated_at="t",
        source_duration_sec=120.0,
        window_sec=60.0,
        composite_method="weighted_sum",
        score_threshold=0.55,
        diversity=DiversityReport(min_gap_sec=30.0, max_clips_per_minute=2.0),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=0.0,
            end_sec=60.0,
            composite_score=0.7,
            signal_contributions={},
            signal_values={},
        ),
        moments=[],
        moment_count=0,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )


def test_moments_result_thumbnail_fields_default_empty() -> None:
    mr = _bare_moments_result()
    assert mr.thumbnail_candidates == []
    assert mr.thumbnail_text_suggestions == []
    assert mr.thumbnail_text_status == "not_generated"


def test_moments_result_dump_includes_thumbnail_fields() -> None:
    dumped = _bare_moments_result().model_dump()
    assert "thumbnail_candidates" in dumped
    assert "thumbnail_text_suggestions" in dumped
    assert "thumbnail_text_status" in dumped


def test_shortform_moments_existing_fields_unchanged() -> None:
    # additive fields must not perturb the existing schema (shortform-consumer regression)
    dumped = _bare_moments_result().model_dump()
    for key in (
        "run_id",
        "mode",
        "source",
        "window_sec",
        "moments",
        "moment_count",
        "best_window",
        "used_fallback",
        "below_threshold_best_candidates",
    ):
        assert key in dumped
    assert dumped["mode"] == "shortform"
    assert dumped["moment_count"] == 0


def test_pre_phase7_moments_json_loads_with_defaults() -> None:
    # A Phase-6 (pre-Phase-7) moments.json has none of the thumbnail_* keys; it must
    # deserialize cleanly through the new MomentsResult with defaults supplied — backward
    # compatibility for cached workspaces (`cme reprocess`). Includes a Phase-4.5 reframe
    # block to mirror a real prior artifact.
    from src.intelligence.ranking import MomentsResult

    legacy = {
        "run_id": "run_old",
        "mode": "shortform",
        "source": "/x/vid.mp4",
        "generated_at": "2026-05-30T00:00:00",
        "source_duration_sec": 120.0,
        "window_sec": 60.0,
        "composite_method": "weighted_sum",
        "score_threshold": 0.55,
        "diversity": {"min_gap_sec": 30.0, "max_clips_per_minute": 2.0},
        "weights": {"configured": {}, "effective": {}, "unavailable_signals": []},
        "best_window": {
            "start_sec": 0.0,
            "end_sec": 60.0,
            "composite_score": 0.7,
            "signal_contributions": {},
            "signal_values": {},
        },
        "moments": [
            {
                "rank": 1,
                "start_sec": 0.0,
                "end_sec": 60.0,
                "duration_sec": 60.0,
                "composite_score": 0.7,
                "scene_snapped": False,
                "signal_contributions": {},
                "reframe": {
                    "method": "blur_pad",
                    "foreground_scale": 1.0,
                    "median_count": 2,
                    "max_spread": 0.4,
                },
            }
        ],
        "moment_count": 1,
        "used_fallback": False,
        "below_threshold_best_candidates": [],
    }

    result = MomentsResult.model_validate(legacy)  # must not raise on the missing new keys

    # new Phase-7 fields default in
    assert result.thumbnail_candidates == []
    assert result.thumbnail_text_suggestions == []
    assert result.thumbnail_text_status == "not_generated"
    # old data preserved, including the Phase 4.5 reframe diagnostic
    assert result.moment_count == 1
    assert result.moments[0].reframe is not None
    assert result.moments[0].reframe.method == "blur_pad"


# --------------------------------------------------------------------------- #
# Phase 7 — standard_longform mode config (the longform knobs, per CP1/D4)
# --------------------------------------------------------------------------- #


def test_standard_longform_has_preferred_duration() -> None:
    from src.config import load_mode_config

    pacing = load_mode_config("standard_longform").pacing
    assert "preferred_duration_sec" in pacing
    assert 180 <= float(pacing["preferred_duration_sec"]) <= 600  # 3-10 min target window


def test_standard_longform_output_thumbnail_counts() -> None:
    from src.config import load_mode_config

    output = load_mode_config("standard_longform").output
    assert int(output["thumbnail_count"]) == 3  # CP1 scope (was 5)
    assert int(output["thumbnail_text_count"]) == 3


def test_standard_longform_output_base_dir_is_longform_standard() -> None:
    from src.config import load_mode_config

    base_dir = str(load_mode_config("standard_longform").output["base_dir"]).replace("\\", "/")
    assert base_dir == "src/output/longform/standard"
