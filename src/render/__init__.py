"""Render layer orchestration.

``run_render`` turns a mode's ranked moments into rendered clip files via a
:class:`RenderBackend` (ffmpeg today; a future Descript backend slots in behind the
same interface). ``build_clip_specs`` is the pure moment->spec mapping (caps each
clip at the platform max, shifts captions to clip-relative time, names files per
the mode pattern). Shortform is the only wired path in Phase 4.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from datetime import datetime
from functools import partial
from pathlib import Path

from ..analysis.subjects import FrameSubjects, SubjectsAnalysis
from ..config import PROJECT_ROOT, Config, ModeConfig, ReframeDecisionConfig
from ..ingestion import Workspace
from ..intelligence.ranking import Moment, MomentsResult, ReframeDecision, write_moments
from ..utils.fileio import read_json
from ..utils.logging import get_logger
from ..utils.parallel import Task, run_graph
from .auto_reframe import ReframePlan, aggregate_window, frames_in_window, plan_clip
from .backend import CaptionStyle, ClipRenderSpec, RenderBackend, RenderFormat
from .ffmpeg_pipeline import FFmpegBackend
from .social_formats import compute_zoom_crop_x
from .subtitles import caption_layout, group_words_into_lines

__all__ = ["build_clip_specs", "run_render"]

_log = get_logger(__name__)

DEFAULT_FILENAME_PATTERN = "{date}_{source_name}_clip_{rank:02d}.mp4"
PLATFORM_MAX_CLIP_SEC = 60.0


def build_clip_specs(
    moments: Sequence[Moment],
    *,
    source: Path,
    words: Sequence[tuple[str, float, float]],
    fmt: RenderFormat,
    caption_style: CaptionStyle,
    resolve_reframe: Callable[[Moment], ReframePlan],
    max_words_per_line: int,
    output_dir: Path,
    source_name: str,
    date: str,
    watermark: Path | None,
    resolve_zoom_crop_x: Callable[[Moment], int | None] | None = None,
    filename_pattern: str = DEFAULT_FILENAME_PATTERN,
    max_duration_sec: float = PLATFORM_MAX_CLIP_SEC,
) -> list[ClipRenderSpec]:
    """Map ranked moments to backend-agnostic clip specs (capped at the platform max).

    ``resolve_reframe`` picks the reframe plan (method/foreground_scale/track_crop) per
    moment — fixed for manual modes, content-adaptive (auto-reframe) for ``auto``.
    ``resolve_zoom_crop_x`` (zoom_crop folder only) supplies the per-moment subject-aware
    crop x; ``None`` (every other path) leaves it unset so the crop stays geometric-centre.
    """
    specs: list[ClipRenderSpec] = []
    for moment in moments:
        end = min(moment.end_sec, moment.start_sec + max_duration_sec)
        clip_words = [
            (text, start - moment.start_sec, word_end - moment.start_sec)
            for text, start, word_end in words
            if moment.start_sec <= start < end
        ]
        captions = group_words_into_lines(clip_words, max_words_per_line)
        name = filename_pattern.format(date=date, source_name=source_name, rank=moment.rank)
        plan = resolve_reframe(moment)
        zoom_crop_x = resolve_zoom_crop_x(moment) if resolve_zoom_crop_x is not None else None
        specs.append(
            ClipRenderSpec(
                source=source,
                start_sec=moment.start_sec,
                end_sec=end,
                fmt=fmt,
                output_path=output_dir / name,
                reframe_method=plan.method,
                foreground_scale=plan.foreground_scale,
                track_crop=plan.track_crop,
                zoom_crop_x=zoom_crop_x,
                captions=captions,
                caption_style=caption_style,
                watermark_path=watermark,
            )
        )
    return specs


def _load_transcript_words(workspace: Workspace) -> list[tuple[str, float, float]]:
    if not workspace.transcript_path.exists():
        return []
    transcript = read_json(workspace.transcript_path)
    return [
        (str(w.get("word", "")).strip(), float(w["start"]), float(w["end"]))
        for seg in transcript.get("segments", [])
        for w in seg.get("words", [])
        if w.get("start") is not None and w.get("end") is not None
    ]


def _load_subjects(workspace: Workspace) -> SubjectsAnalysis | None:
    subjects_path = workspace.analysis_dir / "subjects.json"
    if not subjects_path.exists():
        return None
    return SubjectsAnalysis.model_validate(read_json(subjects_path))


def zoom_crop_x_for_window(
    window_frames: Sequence[FrameSubjects],
    *,
    frame_width: int,
    frame_height: int,
    target_w: int,
    target_h: int,
    foreground_scale: float,
) -> int | None:
    """Subject-aware zoom_crop x for one window: median group-bbox centre -> crop-x.

    Returns ``None`` when no frame in the window has a detected subject, so the caller
    falls back to the geometric-centre crop (current behaviour, no crash).
    """
    centers = [
        (f.group_bbox[0] + f.group_bbox[2]) / 2.0 for f in window_frames if f.group_bbox is not None
    ]
    if not centers:
        return None
    return compute_zoom_crop_x(
        subject_x_source=statistics.median(centers),
        source_w=frame_width,
        source_h=frame_height,
        target_w=target_w,
        target_h=target_h,
        foreground_scale=foreground_scale,
    )


def _reframe_for_moment(
    moment: Moment,
    *,
    reframe_method: str,
    foreground_scale: float,
    fmt: RenderFormat,
    cfg: ReframeDecisionConfig,
    subjects: SubjectsAnalysis | None,
) -> tuple[ReframePlan, ReframeDecision]:
    """Resolve one moment's reframe: the render plan + the diagnostic decision (one pass).

    ``auto`` runs the decision engine over the moment's ``subjects.json`` window (a
    missing/empty window falls back to blur_pad inside ``plan_clip``). Manual methods
    return a fixed plan; their diagnostic carries ``median_count``/``max_spread`` = 0
    (no detection was consulted).
    """
    if reframe_method != "auto":
        plan = ReframePlan(method=reframe_method, foreground_scale=foreground_scale)
        return plan, ReframeDecision(
            method=plan.method,
            foreground_scale=plan.foreground_scale,
            median_count=0,
            max_spread=0.0,
        )

    frames = subjects.frames if subjects is not None else []
    src_width = subjects.frame_width if subjects is not None else fmt.width
    src_height = subjects.frame_height if subjects is not None else fmt.height
    window = frames_in_window(frames, moment.start_sec, moment.end_sec)
    profile = aggregate_window(window)
    plan = plan_clip(
        window,
        clip_start_sec=moment.start_sec,
        src_width=src_width,
        src_height=src_height,
        target_width=fmt.width,
        target_height=fmt.height,
        cfg=cfg,
    )
    return plan, ReframeDecision(
        method=plan.method,
        foreground_scale=plan.foreground_scale,
        median_count=profile.median_count,
        max_spread=profile.max_spread,
    )


def run_render(
    source: Path,
    workspace: Workspace,
    config: Config,
    mode: ModeConfig,
    moments_result: MomentsResult,
    *,
    backend: RenderBackend | None = None,
    on_event: Callable[[str, str], None] | None = None,
) -> list[Path]:
    """Render every kept moment (or the fallback candidates) to clip files; return paths."""
    backend = backend or FFmpegBackend()
    md = mode.model_dump()
    src_dir = PROJECT_ROOT / "src"

    rendering = md.get("rendering", {})
    resolution = rendering.get("resolution", {})
    fmt = RenderFormat(
        width=int(resolution.get("width", 1080)),
        height=int(resolution.get("height", 1920)),
        fps=int(rendering.get("fps", 30)),
        video_codec=rendering.get("video_codec", "h264"),
        audio_codec=rendering.get("audio_codec", "aac"),
        bitrate=rendering.get("bitrate_target"),
    )
    reframe_method = rendering.get("reframe_method", "blur_pad")
    foreground_scale = float(rendering.get("foreground_scale", 1.0))

    composition = md.get("composition", {})
    margin_v, margin_l, margin_r = caption_layout(
        caption_y_position=float(composition.get("caption_y_position", 0.80)),
        caption_safe_width=int(composition.get("caption_safe_width", 900)),
        width=fmt.width,
        height=fmt.height,
    )
    if "crop_focal_y" in composition or "face_fill_target" in composition:
        _log.info(
            "composition: caption zoning active; face-aware framing "
            "(crop_focal_y=%s, face_fill_target=%s) stored but INACTIVE until Phase 5 "
            "face detection",
            composition.get("crop_focal_y"),
            composition.get("face_fill_target"),
        )

    captions_cfg = md.get("captions", {})
    font_rel = captions_cfg.get("font")
    caption_style = CaptionStyle(
        font=(src_dir / font_rel) if font_rel else None,
        font_size=int(captions_cfg.get("font_size", 64)),
        primary_color=captions_cfg.get("font_color", "FFFFFF"),
        outline_color=captions_cfg.get("outline_color", "000000"),
        outline=int(captions_cfg.get("outline_width", 3)),
        margin_v=margin_v,
        margin_l=margin_l,
        margin_r=margin_r,
    )
    max_words = int(captions_cfg.get("max_words_per_line", 4))

    watermark_rel = md.get("branding", {}).get("watermark")
    watermark = (src_dir / watermark_rel) if watermark_rel else None

    output_cfg = md.get("output", {})
    output_base = PROJECT_ROOT / output_cfg.get("base_dir", "src/output/shorts")
    filename_pattern = output_cfg.get("filename_pattern", DEFAULT_FILENAME_PATTERN)
    dual_output = bool(rendering.get("dual_output", False))

    words = _load_transcript_words(workspace) if captions_cfg.get("enabled", True) else []
    to_render: list[Moment] = (
        moments_result.moments or moments_result.below_threshold_best_candidates
    )

    subjects = _load_subjects(workspace) if reframe_method == "auto" else None
    if reframe_method == "auto" and subjects is None:
        _log.warning("reframe_method=auto but subjects.json missing; clips fall back to blur_pad")

    # Resolve each moment's reframe ONCE: the render plan + the diagnostic decision.
    # The decision is written back into moments.json so framing choices are inspectable
    # without eyeballing rendered output (mirrors the per-signal scoring breakdown).
    plans: dict[int, ReframePlan] = {}
    for moment in to_render:
        plan, decision = _reframe_for_moment(
            moment,
            reframe_method=reframe_method,
            foreground_scale=foreground_scale,
            fmt=fmt,
            cfg=config.reframe_decision,
            subjects=subjects,
        )
        plans[id(moment)] = plan
        moment.reframe = decision

    date = datetime.now().strftime("%Y%m%d")

    def specs_for(
        out_dir: Path,
        resolve: Callable[[Moment], ReframePlan],
        resolve_zoom_crop_x: Callable[[Moment], int | None] | None = None,
    ) -> list[ClipRenderSpec]:
        return build_clip_specs(
            to_render,
            source=source,
            words=words,
            fmt=fmt,
            caption_style=caption_style,
            resolve_reframe=resolve,
            resolve_zoom_crop_x=resolve_zoom_crop_x,
            max_words_per_line=max_words,
            output_dir=out_dir,
            source_name=source.stem,
            date=date,
            watermark=watermark,
            filename_pattern=filename_pattern,
        )

    # dual_output renders each moment twice (auto/ + zoom_crop/) with identical filenames
    # for side-by-side comparison; single output goes straight to the base dir.
    auto_dir = output_base / "auto" if dual_output else output_base
    specs = specs_for(auto_dir, lambda moment: plans[id(moment)])
    if dual_output:
        zoom_fs = config.zoom_crop_decision.foreground_scale
        zoom_plan = ReframePlan(method="zoom_crop", foreground_scale=zoom_fs)

        def _zoom_crop_x(moment: Moment) -> int | None:
            # centre zoom_crop on the subject's median x in the moment window; no detection
            # -> None -> geometric centre. auto/ is untouched (it uses its own per-moment plan).
            if subjects is None:
                return None
            window = frames_in_window(subjects.frames, moment.start_sec, moment.end_sec)
            return zoom_crop_x_for_window(
                window,
                frame_width=subjects.frame_width,
                frame_height=subjects.frame_height,
                target_w=fmt.width,
                target_h=fmt.height,
                foreground_scale=zoom_fs,
            )

        specs += specs_for(output_base / "zoom_crop", lambda _moment: zoom_plan, _zoom_crop_x)

    # Render clips concurrently (RENDER lane, videotoolbox-session-safe cap). Each
    # render_clip is unchanged; only the loop is parallelized. Results are collected in
    # spec order so the returned list is deterministic regardless of completion order.
    render_tasks = [
        Task(name=str(i), fn=partial(backend.render_clip, spec), lane="render")
        for i, spec in enumerate(specs)
    ]
    results = run_graph(
        render_tasks,
        max_workers=max(1, config.performance.max_render_workers),
        lane_limits={"render": config.performance.max_render_workers},
        on_event=on_event,
    )
    outputs: list[Path] = []
    for i, spec in enumerate(specs):
        result = results[str(i)]
        if result.error is not None:
            _log.warning(
                "render: clip %s failed (%s); skipping", spec.output_path.name, result.error
            )
            continue
        outputs.append(result.value)

    write_moments(moments_result, workspace.moments_path)  # persist the reframe diagnostics
    _log.info("render: wrote %d clip(s) to %s", len(outputs), output_base)
    return outputs
