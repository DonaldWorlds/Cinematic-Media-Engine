"""Intelligence layer orchestration.

``run_intelligence`` reads the analysis artifacts of one run, scores them with the
mode-specific composite, slides a fixed-length window to pick the strongest clips,
enforces diversity, ranks, and writes ``moments.json``. It reads only JSON (never
imports the heavy analysis modules), so signals flow as primitives via SignalData.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..analysis import AnalysisOutputs
from ..config import Config, ModeConfig
from ..ingestion import Workspace
from ..utils.fileio import read_json
from ..utils.logging import get_logger
from .cinematic_score import score_cinematic
from .clustering import select_diverse, slide_windows
from .pacing_engine import build_pacing_plan
from .ranking import (
    MomentsResult,
    build_moments_result,
    select_moments,
    to_best_window,
    write_moments,
)
from .scoring import ScoredTimeline, SignalData, Span, WordSpan, score_standard
from .viral_score import score_shortform

__all__ = ["run_intelligence"]

_log = get_logger(__name__)

# Snap a window boundary to a scene cut within this tolerance (no-op if none near).
SCENE_SNAP_TOLERANCE_SEC = 2.0

_SCORERS: dict[str, Callable[[SignalData, dict[str, float]], ScoredTimeline]] = {
    "shortform": score_shortform,
    "cinematic_longform": score_cinematic,
    "standard_longform": score_standard,
}


def _load_signal_data(workspace: Workspace, outputs: AnalysisOutputs) -> SignalData:
    """Read the analysis JSON artifacts into a primitive :class:`SignalData`."""
    librosa = read_json(outputs.librosa_path)
    duration = float(librosa["duration_sec"])
    if workspace.metadata_path.exists():
        meta = read_json(workspace.metadata_path)
        duration = float(meta.get("duration_sec", duration))

    data = SignalData(
        duration_sec=duration,
        audio_times=list(librosa["times_sec"]),
        energy=list(librosa["energy"]),
        speech_intensity=list(librosa["speech_intensity"]),
        peak_times=[float(p["time_sec"]) for p in librosa["peaks"]],
    )

    if outputs.opencv_path is not None:
        opencv = read_json(outputs.opencv_path)
        data.motion_times = list(opencv["motion_times_sec"])
        data.motion = list(opencv["motion"])
        data.scene_cuts_sec = list(opencv["scene_cuts_sec"])

    if outputs.aesthetics_path is not None:
        aesthetics = read_json(outputs.aesthetics_path)
        data.aesthetics_times = list(aesthetics["times_sec"])
        data.aesthetics = {k: list(v) for k, v in aesthetics["metrics"].items()}

    if outputs.transcript_path is not None and outputs.transcript_path.exists():
        transcript = read_json(outputs.transcript_path)
        data.has_transcript = True
        data.words = [
            WordSpan(start=float(w["start"]), end=float(w["end"]), score=w.get("score"))
            for seg in transcript["segments"]
            for w in seg.get("words", [])
            if w.get("start") is not None and w.get("end") is not None
        ]

    if outputs.hooks_path is not None:
        hooks = read_json(outputs.hooks_path)
        data.hook_times = [float(h["time_sec"]) for h in hooks["hooks"]]
        data.quotable_spans = [
            Span(start=float(q["start_sec"]), end=float(q["end_sec"])) for q in hooks["quotables"]
        ]

    return data


def run_intelligence(
    source: Path,
    workspace: Workspace,
    config: Config,
    mode: ModeConfig,
    outputs: AnalysisOutputs,
) -> MomentsResult:
    """Score the run, select the best diverse clips, and write moments.json."""
    data = _load_signal_data(workspace, outputs)

    scorer = _SCORERS.get(mode.mode_name, score_standard)
    scored = scorer(data, mode.scoring_weights)
    _log.info(
        "intelligence: mode=%s, signals=%s, unavailable=%s",
        mode.mode_name,
        sorted(scored.timelines),
        scored.unavailable,
    )

    mode_dump: dict[str, Any] = mode.model_dump()
    plan = build_pacing_plan(
        mode.pacing,
        diversity=mode_dump.get("diversity"),
        score_threshold=mode_dump.get("score_threshold"),
        target_clips=mode_dump.get("target_clips_per_session"),
        top_n_fallback=mode_dump.get("top_n_fallback"),
        window_aggregation=mode_dump.get("window_aggregation", config.scoring.window_aggregation),
        window_percentile=mode_dump.get("window_percentile", config.scoring.window_percentile),
        default_min_gap_sec=config.scoring.cluster_min_gap_sec,
        default_threshold=config.scoring.default_threshold,
    )

    candidates = slide_windows(
        scored,
        window_sec=plan.window_sec,
        step_sec=plan.step_sec,
        duration_sec=data.duration_sec,
        method=plan.window_aggregation,
        percentile=plan.window_percentile,
    )
    best_window = to_best_window(max(candidates, key=lambda c: c.score))
    selected = select_diverse(
        candidates, min_gap_sec=plan.min_gap_sec, max_clips_per_minute=plan.max_clips_per_minute
    )
    moments, fallback, used_fallback = select_moments(
        selected,
        score_threshold=plan.score_threshold,
        max_clips=plan.max_clips,
        top_n_fallback=plan.top_n_fallback,
        scene_cuts=data.scene_cuts_sec,
        snap_tolerance_sec=SCENE_SNAP_TOLERANCE_SEC,
    )

    result = build_moments_result(
        run_id=workspace.run_id,
        mode=mode.mode_name,
        source=str(source),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        source_duration_sec=data.duration_sec,
        composite_method=config.scoring.composite_method,
        scored=scored,
        plan=plan,
        best_window=best_window,
        moments=moments,
        below_threshold_best_candidates=fallback,
        used_fallback=used_fallback,
    )
    write_moments(result, workspace.moments_path)
    _log.info(
        "intelligence: %d moment(s) (fallback=%s), best window %.3f vs threshold %.3f -> %s",
        result.moment_count,
        used_fallback,
        best_window.composite_score,
        plan.score_threshold,
        workspace.moments_path,
    )
    return result
