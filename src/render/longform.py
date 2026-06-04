"""standard_longform render path (Descript-prep) — isolated from shortform.

Phase 7: produce ONE 16:9 native cut per source from the single best-scoring window,
plus thumbnail-frame candidates and short quotable text suggestions for Descript. No
reframe, no burned captions, no watermark, no color grade — Descript owns all final
polish (see KNOWLEDGE.md "Descript-prep" decision).

Deliberately separate from the frozen shortform render path (``render/__init__.py`` +
``ffmpeg_pipeline.py``): it reuses the videotoolbox->libx264 fallback PATTERN but shares
no code, so it cannot regress shortform output. Multi-window stitching is Phase 7b.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from ..config import PROJECT_ROOT, Config, ModeConfig
from ..ingestion import Workspace
from ..ingestion.workspace import (
    artifact_fingerprint,
    is_artifact_fresh,
    read_cache_manifest,
    write_cache_manifest,
)
from ..intelligence.ranking import Moment, MomentsResult, write_moments
from ..intelligence.thumbnail_text import ThumbnailText, extract_thumbnail_text
from ..utils.ffmpeg import FFmpegError, run_ffmpeg
from ..utils.fileio import ensure_dir, read_json, write_json
from ..utils.logging import get_logger
from .backend import RenderFormat
from .color_pipeline import get_correction_for_mode
from .thumbnails import extract_thumbnails, select_thumbnail_frames

__all__ = [
    "GRADE_FULL_FILE_SEC",
    "LongformBackend",
    "LongformClipSpec",
    "LutGrade",
    "build_grade_command",
    "build_longform_command",
    "resolve_lut_grade",
    "run_grade",
    "run_longform_render",
    "select_best_window",
]

# ffmpeg lut3d interpolation modes (Phase 8 cinematic grade defaults to tetrahedral).
LutInterp = Literal["nearest", "trilinear", "tetrahedral", "pyramid", "prism"]

_log = get_logger(__name__)

DEFAULT_ENCODER = "h264_videotoolbox"  # M1 hardware H.264
FALLBACK_ENCODER = "libx264"  # software fallback if videotoolbox fails (SPEC §9)
DEFAULT_FILENAME_PATTERN = "{date}_{source_name}_standard_longform.mp4"
DEFAULT_TARGET_DURATION_SEC = 300.0  # 5 min, if the mode doesn't set preferred_duration_sec
DEFAULT_THUMBNAIL_COUNT = 3
DEFAULT_THUMBNAIL_TEXT_COUNT = 3
THUMBNAIL_MIN_GAP_SEC = 5.0  # keep extracted frames visually distinct across the window
GRADE_FULL_FILE_SEC = 1e9  # `cme grade` sentinel: "sample to EOF" — window_sample_times
# clamps this down to the source's real readable duration (no EOF bias).


class LutGrade(BaseModel):
    """A single 3D LUT color grade applied via ffmpeg ``lut3d`` (Phase 8 cinematic).

    ``path`` is the .cube file (resolved absolute when built from mode config). The
    Phase-8 grade is single-stage at full intensity — no blend, no two-stage chain.
    """

    path: Path
    interp: LutInterp = "tetrahedral"


class LongformClipSpec(BaseModel):
    """A backend-agnostic description of one 16:9 longform cut (no reframe/captions)."""

    source: Path
    start_sec: float
    end_sec: float
    fmt: RenderFormat
    output_path: Path
    lut: LutGrade | None = None  # cinematic_longform sets this; standard stays None (ungraded)
    correction: str = ""  # Phase 10: technical-correction filters, prepended BEFORE lut3d


def resolve_lut_grade(color_grade: Mapping[str, Any]) -> LutGrade | None:
    """Build a ``LutGrade`` from a mode's ``color_grade`` config, or None if disabled.

    Resolves ``lut_path`` against ``PROJECT_ROOT`` (like the output base) and checks
    existence at call time — render entry, not config load — so a deleted/missing LUT
    fails loudly with its resolved path before any ffmpeg work starts.
    """
    if not color_grade.get("enabled"):
        return None
    lut_path = color_grade.get("lut_path")
    if not lut_path:
        raise ValueError("color_grade.enabled is true but color_grade.lut_path is missing")
    resolved = (PROJECT_ROOT / str(lut_path)).resolve()
    if not resolved.is_file():
        raise ValueError(f"color_grade.lut_path file not found: {resolved}")
    return LutGrade(path=resolved, interp=color_grade.get("interp", "tetrahedral"))


def build_longform_command(spec: LongformClipSpec, *, encoder: str) -> list[str]:
    """ffmpeg args (sans binary) to cut [start,end] and re-encode to 16:9 at fmt size.

    A plain ``-vf`` chain (NOT ``-filter_complex``): no reframe, no caption burn, no
    watermark overlay — Descript handles all downstream polish. When ``spec.lut`` is
    set, a single ``lut3d`` grade is prepended BEFORE scale, so the colour grade is
    applied at source resolution and then downsampled. When ``spec.correction`` is set,
    Phase-10 technical-correction filters are prepended BEFORE the LUT (LUTs assume
    normalized input — KNOWLEDGE.md Phase 10 ordering rule).
    """
    duration = spec.end_sec - spec.start_sec
    vf = f"scale={spec.fmt.width}:{spec.fmt.height}"
    if spec.lut is not None:
        vf = f"lut3d={spec.lut.path}:interp={spec.lut.interp},{vf}"
    if spec.correction:
        vf = f"{spec.correction},{vf}"
    args = [
        "-y",
        "-ss",
        f"{spec.start_sec:.3f}",
        "-i",
        str(spec.source),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-r",
        str(spec.fmt.fps),
        "-c:v",
        encoder,
    ]
    if spec.fmt.bitrate:
        args += ["-b:v", spec.fmt.bitrate]
    args += ["-c:a", spec.fmt.audio_codec, "-movflags", "+faststart", str(spec.output_path)]
    return args


class LongformBackend:
    """Renders a longform cut with ffmpeg (videotoolbox -> libx264 fallback, SPEC §9)."""

    def __init__(
        self, *, encoder: str = DEFAULT_ENCODER, fallback_encoder: str = FALLBACK_ENCODER
    ) -> None:
        self.encoder = encoder
        self.fallback_encoder = fallback_encoder

    def render_clip(self, spec: LongformClipSpec) -> Path:
        """Render the 16:9 cut and return its path."""
        ensure_dir(spec.output_path.parent)
        try:
            run_ffmpeg(build_longform_command(spec, encoder=self.encoder))
        except FFmpegError as err:
            if self.encoder == self.fallback_encoder:
                raise
            _log.warning(
                "encoder %s failed (%s); retrying with %s",
                self.encoder,
                err,
                self.fallback_encoder,
            )
            run_ffmpeg(build_longform_command(spec, encoder=self.fallback_encoder))
        return spec.output_path


def select_best_window(moments_result: MomentsResult) -> Moment:
    """The single window to cut: the top moment, else the best below-threshold candidate."""
    pool = moments_result.moments or moments_result.below_threshold_best_candidates
    if not pool:
        raise ValueError("no moments or fallback candidates to render")
    return pool[0]


def _thumbnail_candidates(
    source: Path,
    workspace: Workspace,
    thumb_dir: Path,
    *,
    start: float,
    end: float,
    count: int,
    fmt: RenderFormat,
) -> list[Path]:
    """Select the most aesthetic frames in the window and export them as JPEGs."""
    aesthetics_path = workspace.analysis_dir / "aesthetics.json"
    times: list[float] = []
    metrics: dict[str, list[float]] = {}
    if aesthetics_path.exists():
        data = read_json(aesthetics_path)
        times = [float(t) for t in data.get("times_sec", [])]
        metrics = {k: [float(x) for x in v] for k, v in data.get("metrics", {}).items()}
    frame_times = select_thumbnail_frames(
        times, metrics, start=start, end=end, count=count, min_gap_sec=THUMBNAIL_MIN_GAP_SEC
    )
    return extract_thumbnails(
        source, frame_times, thumb_dir, width=fmt.width, height=fmt.height, source_name=source.stem
    )


def _thumbnail_text(workspace: Workspace, start: float, end: float, count: int) -> ThumbnailText:
    """Quotable text suggestions for the window (transcript + hooks quotables)."""
    if not workspace.transcript_path.exists():
        return extract_thumbnail_text(
            transcript_available=False,
            words=[],
            quotables=[],
            window_start=start,
            window_end=end,
            count=count,
        )
    transcript = read_json(workspace.transcript_path)
    words = [
        (
            str(w.get("word", "")).strip(),
            float(w["start"]),
            float(w["end"]),
            float(w.get("score") or 0.0),
        )
        for seg in transcript.get("segments", [])
        for w in seg.get("words", [])
        if w.get("start") is not None and w.get("end") is not None
    ]
    quotables: list[tuple[float, float]] = []
    hooks_path = workspace.analysis_dir / "hooks.json"
    if hooks_path.exists():
        hooks = read_json(hooks_path)
        quotables = [
            (float(q["start_sec"]), float(q["end_sec"])) for q in hooks.get("quotables", [])
        ]
    return extract_thumbnail_text(
        transcript_available=True,
        words=words,
        quotables=quotables,
        window_start=start,
        window_end=end,
        count=count,
    )


def _resolve_correction(
    source: Path,
    workspace: Workspace,
    mode: ModeConfig,
    window_start: float,
    window_end: float,
) -> str:
    """Window-tuned technical-correction filter, cached per source+window+config (Phase 10).

    Returns ``""`` when correction is disabled (no cache artifact written). Otherwise reuses
    a cached ``analysis/color_correction.json`` when the source, window, and colour-pipeline
    config are unchanged (Phase-6 fingerprint), else recomputes and refreshes the cache.
    """
    cp_cfg = mode.color_pipeline
    if not cp_cfg.technical_correction.enabled:
        return ""
    section: dict[str, Any] = {
        "color_pipeline": cp_cfg.model_dump(),
        "window": [round(window_start, 3), round(window_end, 3)],
    }
    fingerprint = artifact_fingerprint(source, section)
    manifest = read_cache_manifest(workspace)
    cache_path = workspace.analysis_dir / "color_correction.json"
    if is_artifact_fresh("color_correction", fingerprint, manifest) and cache_path.exists():
        return str(read_json(cache_path).get("filter", ""))
    correction = get_correction_for_mode(source, window_start, window_end, cp_cfg)
    ensure_dir(workspace.analysis_dir)
    write_json(cache_path, {"filter": correction})
    write_cache_manifest(workspace, {**manifest, "color_correction": fingerprint})
    return correction


def run_longform_render(
    source: Path,
    workspace: Workspace,
    config: Config,
    mode: ModeConfig,
    moments_result: MomentsResult,
    *,
    backend: LongformBackend | None = None,
    on_event: Callable[[str, str], None] | None = None,
) -> list[Path]:
    """Render the single best 16:9 window + extract thumbnail frames & text suggestions."""
    backend = backend or LongformBackend()
    md = mode.model_dump()
    lut = resolve_lut_grade(md.get("color_grade", {}))  # fail fast before any ffmpeg work
    rendering = md.get("rendering", {})
    resolution = rendering.get("resolution", {})
    fmt = RenderFormat(
        width=int(resolution.get("width", 1920)),
        height=int(resolution.get("height", 1080)),
        fps=int(rendering.get("fps", 30)),
        video_codec=rendering.get("video_codec", "h264"),
        audio_codec=rendering.get("audio_codec", "aac"),
        bitrate=rendering.get("bitrate_target"),
    )

    output_cfg = md.get("output", {})
    output_base = PROJECT_ROOT / str(output_cfg.get("base_dir", "src/output/longform/standard"))
    filename_pattern = output_cfg.get("filename_pattern", DEFAULT_FILENAME_PATTERN)
    thumb_count = int(output_cfg.get("thumbnail_count", DEFAULT_THUMBNAIL_COUNT))
    text_count = int(output_cfg.get("thumbnail_text_count", DEFAULT_THUMBNAIL_TEXT_COUNT))

    pacing = md.get("pacing", {})
    target_duration = float(pacing.get("preferred_duration_sec") or DEFAULT_TARGET_DURATION_SEC)

    best = select_best_window(moments_result)
    end = min(best.end_sec, best.start_sec + target_duration)
    correction = _resolve_correction(source, workspace, mode, best.start_sec, end)
    date = datetime.now().strftime("%Y%m%d")
    name = filename_pattern.format(date=date, source_name=source.stem)
    spec = LongformClipSpec(
        source=source,
        start_sec=best.start_sec,
        end_sec=end,
        fmt=fmt,
        output_path=output_base / name,
        lut=lut,
        correction=correction,
    )

    ensure_dir(output_base)
    output_path = backend.render_clip(spec)

    candidates = _thumbnail_candidates(
        source,
        workspace,
        output_base / "thumbnails",
        start=best.start_sec,
        end=end,
        count=thumb_count,
        fmt=fmt,
    )
    text = _thumbnail_text(workspace, best.start_sec, end, text_count)

    moments_result.thumbnail_candidates = [str(p) for p in candidates]
    moments_result.thumbnail_text_suggestions = text.suggestions
    moments_result.thumbnail_text_status = text.status
    write_moments(moments_result, workspace.moments_path)

    _log.info(
        "longform: 1 clip -> %s (%.0f-%.0fs); %d thumbnail(s); text=%s",
        output_path,
        best.start_sec,
        end,
        len(candidates),
        text.status,
    )
    return [output_path]


def _crf_to_vt_quality(crf: int) -> int:
    """Map a libx264 CRF (0-51, lower=better) to a videotoolbox ``-q:v`` (1-100, higher=better).

    Keeps ``--quality`` same-direction across encoders: CRF 18 -> q:v 65 (the tuned 4K-master
    constant quality); lower CRF -> higher q:v.
    """
    return max(1, min(100, round((51 - crf) / 51 * 100)))


def build_grade_command(
    source: Path,
    output: Path,
    *,
    lut: LutGrade,
    fmt: RenderFormat,
    correction: str = "",
    scale: bool = False,
    crf: int = 18,
    bitrate: str | None = None,
    encoder: str = "libx264",
    preset: str = "medium",
) -> list[str]:
    """ffmpeg args to grade a FULL file (no ``-ss``/``-t`` cut, native fps preserved).

    vf is ``[correction,]lut3d[,scale]`` — same ordering as the cut path
    (``build_longform_command``) but over the whole input. Quality flags branch on
    ``encoder``: libx264 -> ``-crf``/``-preset`` (resolution-independent CRF master);
    h264_videotoolbox -> ``-q:v`` constant quality ONLY (never libx264 flags). ``bitrate``
    forces ABR and wins on both. Audio is stream-copied (video-only change).
    """
    vf = f"lut3d={lut.path}:interp={lut.interp}"
    if scale:
        vf = f"{vf},scale={fmt.width}:{fmt.height}"
    if correction:
        vf = f"{correction},{vf}"
    args = ["-y", "-i", str(source), "-vf", vf, "-c:v", encoder]
    if bitrate:
        args += ["-b:v", bitrate]  # explicit ABR override — wins on BOTH encoders
    elif encoder == "libx264":
        args += ["-crf", str(crf), "-preset", preset]  # software CRF master
    else:  # h264_videotoolbox: constant quality ONLY — never libx264's -crf/-preset
        args += ["-q:v", str(_crf_to_vt_quality(crf))]
    args += ["-c:a", "copy", "-movflags", "+faststart", str(output)]
    return args


def run_grade(
    source: Path,
    output: Path,
    config: Config,
    mode: ModeConfig,
    *,
    correct: bool = True,
    scale_to_mode: bool = False,
    quality: int = 18,
    bitrate: str | None = None,
    hardware: bool = True,
) -> Path:
    """Apply ``mode``'s LUT grade to a FULL input file — no selection, no cut.

    Correction (Phase-10 eq/colorbalance/curves) is ON by default, auto-detected over the
    input's own ``[0, duration]`` (``get_correction_for_mode`` clamps the sentinel end down to
    the real readable duration). Native resolution unless ``scale_to_mode``. Encodes on the M1
    media engine (``h264_videotoolbox``, ``-q:v`` constant quality) by default — software 4K
    libx264 exhausts an 8GB M1; ``hardware=False`` switches to libx264 CRF. ``bitrate`` forces
    ABR. ONE ``run_ffmpeg`` call with NO fallback: a hardware error fails loud (no libx264 retry
    back into the 4K crash). Fails loud if the mode has no LUT or the source can't be read. No
    workspace / no cache — each call is independent (per-file).
    """
    md = mode.model_dump()
    lut = resolve_lut_grade(md.get("color_grade", {}))
    if lut is None:
        raise ValueError(
            f"mode {mode.mode_name!r} has no color_grade/LUT to apply (use cinematic_longform)"
        )
    rendering = md.get("rendering", {})
    resolution = rendering.get("resolution", {})
    fmt = RenderFormat(
        width=int(resolution.get("width", 1920)),
        height=int(resolution.get("height", 1080)),
        fps=int(rendering.get("fps", 30)),
        video_codec=rendering.get("video_codec", "h264"),
        audio_codec=rendering.get("audio_codec", "aac"),
        bitrate=rendering.get("bitrate_target"),
    )
    correction = (
        get_correction_for_mode(source, 0.0, GRADE_FULL_FILE_SEC, mode.color_pipeline)
        if correct
        else ""
    )
    encoder = "h264_videotoolbox" if hardware else "libx264"
    cmd = build_grade_command(
        source,
        output,
        lut=lut,
        fmt=fmt,
        correction=correction,
        scale=scale_to_mode,
        crf=quality,
        bitrate=bitrate,
        encoder=encoder,
    )
    ensure_dir(output.parent)
    run_ffmpeg(cmd)
    _log.info(
        "grade: %s -> %s (encoder=%s, correct=%s, scale_to_mode=%s)",
        source,
        output,
        encoder,
        correct,
        scale_to_mode,
    )
    return output
