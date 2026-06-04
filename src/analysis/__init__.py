"""Analysis layer orchestration (Phase 6: cached + parallel).

``run_analysis`` runs the per-signal modules over one ingested workspace and writes
their JSON artifacts. Two Phase-6 properties:

* **Per-artifact caching** — each stage's fingerprint (source identity + its relevant
  config) is checked against the workspace cache manifest; unchanged stages are served
  from disk (recorded in ``from_cache``) and only stale stages re-run. ``fresh=True``
  bypasses the cache entirely.
* **Threaded lane scheduling** — independent stages run concurrently via
  ``utils.parallel.run_graph`` on threads (heavy native work releases the GIL; one
  process loads the cv2/av dylibs once — see KNOWLEDGE.md / CP1.5). The HEAVY lane
  (whisper, subjects) is capped by a semaphore so only one GB-class model loads at a
  time on 8GB; the LIGHT lane (librosa, opencv, aesthetics, hooks) fills the rest.
  ``hooks`` depends on the transcript: it waits for ``whisper`` when that re-runs, else
  reads the cached ``transcript.json``. A stage that fails is isolated; its dependents
  are skipped and its manifest entry is left stale so the next run retries it (SPEC §9).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import Config, ModeConfig
from ..ingestion import Workspace
from ..ingestion.workspace import (
    artifact_fingerprint,
    is_artifact_fresh,
    read_cache_manifest,
    write_cache_manifest,
)
from ..utils.logging import get_logger
from ..utils.parallel import Task, run_graph

__all__ = ["AnalysisOutputs", "run_analysis", "should_transcribe"]

_log = get_logger(__name__)


class AnalysisOutputs(BaseModel):
    """Paths to the artifacts produced by one analysis run (None = not run/failed)."""

    librosa_path: Path
    opencv_path: Path | None = None
    aesthetics_path: Path | None = None
    subjects_path: Path | None = None
    transcript_path: Path | None = None
    hooks_path: Path | None = None
    transcribed: bool = False
    from_cache: set[str] = Field(default_factory=set)  # artifact names served from cache


@dataclass
class _Stage:
    """One analysis stage: its cache key inputs, lane, deps, and compute closure."""

    name: str
    lane: str
    section: dict[str, object]
    path: Path
    run: Callable[[], Path]
    deps: set[str] = field(default_factory=set)


def should_transcribe(mode: ModeConfig, *, transcribe_override: bool = False) -> bool:
    """Whether to transcribe: shortform always; longform only via --transcribe."""
    return mode.transcription.enabled or transcribe_override


def _build_stages(
    source: Path,
    workspace: Workspace,
    config: Config,
    *,
    transcribed: bool,
) -> list[_Stage]:
    """Assemble the active stages for this run (heavy import of analyze_* is local)."""
    from .aesthetics import analyze_aesthetics, write_aesthetics_json
    from .hooks import analyze_hooks, write_hooks_json
    from .librosa_analysis import analyze_audio, write_librosa_json
    from .opencv import analyze_video, write_opencv_json
    from .subjects import detect_subjects, write_subjects_json
    from .whisper import TranscriptResult, transcribe_audio, write_transcript_json

    analysis_dir = workspace.analysis_dir
    stages: list[_Stage] = [
        _Stage(
            name="librosa",
            lane="light",
            section=config.audio.model_dump(),
            path=analysis_dir / "librosa.json",
            run=lambda: write_librosa_json(
                analyze_audio(workspace.audio_path, config.audio), analysis_dir
            ),
        )
    ]
    if config.opencv.enabled:
        stages.append(
            _Stage(
                name="opencv",
                lane="light",
                section=config.opencv.model_dump(),
                path=analysis_dir / "opencv.json",
                run=lambda: write_opencv_json(analyze_video(source, config.opencv), analysis_dir),
            )
        )
    if config.aesthetics.enabled:
        stages.append(
            _Stage(
                name="aesthetics",
                lane="light",
                section=config.aesthetics.model_dump(),
                path=analysis_dir / "aesthetics.json",
                run=lambda: write_aesthetics_json(
                    analyze_aesthetics(source, config.aesthetics), analysis_dir
                ),
            )
        )
    # whisper before subjects: the long pole grabs the single HEAVY slot first.
    if transcribed:
        stages.append(
            _Stage(
                name="whisper",
                lane="heavy",
                section=config.transcription.model_dump(),
                path=workspace.transcript_path,
                run=lambda: write_transcript_json(
                    transcribe_audio(workspace.audio_path, config.transcription),
                    workspace.transcript_path,
                ),
            )
        )
    if config.subjects.enabled:
        stages.append(
            _Stage(
                name="subjects",
                lane="heavy",
                section=config.subjects.model_dump(),
                path=analysis_dir / "subjects.json",
                run=lambda: write_subjects_json(
                    detect_subjects(source, config.subjects), analysis_dir
                ),
            )
        )
    if transcribed and config.hooks.enabled:

        def _run_hooks() -> Path:
            transcript = TranscriptResult.model_validate_json(
                workspace.transcript_path.read_text(encoding="utf-8")
            )
            return write_hooks_json(analyze_hooks(transcript, config.hooks), analysis_dir)

        stages.append(
            _Stage(
                name="hooks",
                lane="light",
                section=config.hooks.model_dump(),
                path=analysis_dir / "hooks.json",
                run=_run_hooks,
                deps={"whisper"},  # filtered to whatever actually re-runs this round
            )
        )
    return stages


def run_analysis(
    source: Path,
    workspace: Workspace,
    config: Config,
    mode: ModeConfig,
    *,
    transcribe_override: bool = False,
    fresh: bool = False,
    on_event: Callable[[str, str], None] | None = None,
) -> AnalysisOutputs:
    """Run the analysis layer over an ingested workspace, reusing valid cached artifacts."""
    transcribed = should_transcribe(mode, transcribe_override=transcribe_override)
    stages = _build_stages(source, workspace, config, transcribed=transcribed)
    by_name = {s.name: s for s in stages}

    manifest = read_cache_manifest(workspace)
    fingerprints = {s.name: artifact_fingerprint(source, s.section) for s in stages}

    # Cache miss = forced fresh, missing artifact, or fingerprint mismatch...
    to_run = {
        s.name
        for s in stages
        if fresh
        or not s.path.exists()
        or not is_artifact_fresh(s.name, fingerprints[s.name], manifest)
    }
    # ...and a stage whose dependency re-runs must re-run too (its input changed).
    changed = True
    while changed:
        changed = False
        for s in stages:
            if s.name not in to_run and (s.deps & to_run):
                to_run.add(s.name)
                changed = True

    from_cache = {s.name for s in stages if s.name not in to_run}
    _log.info(
        "analysis: %d stage(s) to run %s; %d cached %s",
        len(to_run),
        sorted(to_run),
        len(from_cache),
        sorted(from_cache),
    )

    tasks = [
        Task(name=s.name, fn=s.run, deps=(s.deps & to_run), lane=s.lane)
        for s in stages
        if s.name in to_run
    ]
    max_workers = config.performance.max_workers if config.performance.parallel_analysis else 1
    results = run_graph(
        tasks,
        max_workers=max_workers,
        lane_limits={"heavy": config.performance.max_heavy_workers},
        on_event=on_event,
    )

    for name in to_run:
        result = results[name]
        if result.error is not None:
            _log.warning(
                "analysis: stage %s failed (%s); leaving it stale to retry", name, result.error
            )
        elif result.skipped:
            _log.warning("analysis: stage %s skipped (a dependency failed)", name)
        else:
            manifest[name] = fingerprints[name]  # only successful stages update the manifest
    write_cache_manifest(workspace, manifest)

    def _available(name: str) -> Path | None:
        if name not in by_name:
            return None
        if name in from_cache:
            return by_name[name].path
        result = results[name]
        return by_name[name].path if (result.error is None and not result.skipped) else None

    librosa_path = _available("librosa")
    if librosa_path is None:  # librosa is the required backbone — surface its failure
        raise results["librosa"].error or RuntimeError("librosa analysis produced no output")

    return AnalysisOutputs(
        librosa_path=librosa_path,
        opencv_path=_available("opencv"),
        aesthetics_path=_available("aesthetics"),
        subjects_path=_available("subjects"),
        transcript_path=_available("whisper"),
        hooks_path=_available("hooks"),
        transcribed=transcribed,
        from_cache=from_cache,
    )
