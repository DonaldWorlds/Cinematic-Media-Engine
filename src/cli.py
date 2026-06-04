"""Typer CLI entry point for the Cinematic Media Engine (``cme``)."""

from pathlib import Path

import typer
from rich.console import Console

from .analysis import AnalysisOutputs, run_analysis
from .config import load_config, load_mode_config
from .descript.handoff import dispatch_polish
from .ingestion import ingest
from .ingestion.workspace import (
    artifact_fingerprint,
    clean_workspaces,
    is_artifact_fresh,
    parse_age,
    read_cache_manifest,
    workspace_from_dir,
)
from .intelligence import run_intelligence
from .render import run_render
from .render.longform import run_grade, run_longform_render
from .utils.fileio import read_json
from .utils.keepawake import keep_awake
from .utils.logging import setup_logging
from .utils.progress import clip_progress, stage_progress

app = typer.Typer(
    help="Cinematic Media Engine — one engine, three content modes.",
    no_args_is_help=True,
)
workspace_app = typer.Typer(help="Workspace management.", no_args_is_help=True)
app.add_typer(workspace_app, name="workspace")

console = Console()

# Artifact -> the config section whose change should invalidate it (for `workspace info`).
_ARTIFACT_SECTIONS = {
    "librosa": "audio",
    "opencv": "opencv",
    "aesthetics": "aesthetics",
    "subjects": "subjects",
    "whisper": "transcription",
    "hooks": "hooks",
}
_ARTIFACT_FILES = {
    "librosa": "analysis/librosa.json",
    "opencv": "analysis/opencv.json",
    "aesthetics": "analysis/aesthetics.json",
    "subjects": "analysis/subjects.json",
    "whisper": "transcript.json",
    "hooks": "analysis/hooks.json",
}


def _expected_clip_count(mode_cfg: object, moments: object) -> int:
    kept = getattr(moments, "moments", None) or getattr(
        moments, "below_threshold_best_candidates", []
    )
    dual = bool(mode_cfg.model_dump().get("rendering", {}).get("dual_output", False))  # type: ignore[attr-defined]
    return len(kept) * (2 if dual else 1)


# Phase 9: polish is longform-only. Shortform clips are cut FROM polished longform output
# and inherit polish through the cut, so polishing them again is redundant paid work.
_SHORTFORM_POLISH_ERROR = (
    "Error: --polish is not supported for shortform mode.\n"
    "Shortform clips inherit polish from their source longform.\n"
    "To produce polished shortform:\n"
    "  1. Run --polish on standard_longform or cinematic_longform first\n"
    "  2. Then run shortform mode on the polished longform output as source"
)


@app.command()
def process(
    source: Path = typer.Argument(..., help="Source video file."),
    mode: list[str] = typer.Option(["standard_longform"], "--mode", help="Content mode(s)."),
    top_n: int | None = typer.Option(None, "--top-n", help="Override clip count (later phase)."),
    no_twelvelabs: bool = typer.Option(
        False, "--no-twelvelabs", help="Skip TwelveLabs (later phase)."
    ),
    transcribe: bool = typer.Option(
        False, "--transcribe", help="Force transcription for longform modes."
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Ignore cached analysis; re-ingest and re-analyze from scratch."
    ),
    polish: bool = typer.Option(
        False,
        "--polish",
        help="Hand the rendered longform cut to Descript for polish (longform only).",
    ),
) -> None:
    """Ingest a source video then run analysis -> intelligence -> render (cache-aware)."""
    config = load_config()
    setup_logging(config.logging.level, rich_console=config.logging.rich_console)
    if polish and "shortform" in mode:
        console.print(f"[red]{_SHORTFORM_POLISH_ERROR}[/red]")
        raise typer.Exit(1)
    with keep_awake():
        for mode_name in mode:
            mode_cfg = load_mode_config(mode_name)  # validates the mode exists
            console.print(
                f"[bold]Processing[/bold] {source} [dim](mode={mode_cfg.mode_name})[/dim]"
            )
            workspace = ingest(source, config, fresh=fresh)
            console.print(f"  workspace: {workspace.root}")

            with stage_progress() as on_event:
                outputs = run_analysis(
                    source,
                    workspace,
                    config,
                    mode_cfg,
                    transcribe_override=transcribe,
                    fresh=fresh,
                    on_event=on_event,
                )
            cached = sorted(outputs.from_cache)
            console.print(
                f"  analysis:  {workspace.analysis_dir} "
                f"[dim](cached: {', '.join(cached) if cached else 'none'})[/dim]"
            )
            if outputs.transcribed:
                console.print(f"  transcript: {outputs.transcript_path}")

            moments = run_intelligence(source, workspace, config, mode_cfg, outputs)
            console.print(f"  moments:   {workspace.moments_path} ({moments.moment_count} kept)")
            if moments.moments:
                top = moments.moments[0]
                console.print(
                    f"  top clip:  {top.start_sec:.1f}-{top.end_sec:.1f}s "
                    f"score={top.composite_score:.3f}"
                )
            else:
                bw = moments.best_window
                n = len(moments.below_threshold_best_candidates)
                console.print(
                    f"  [yellow]no moment cleared threshold {moments.score_threshold:.2f}; "
                    f"returning {n} below-threshold candidate(s) "
                    f"(best window {bw.start_sec:.0f}-{bw.end_sec:.0f}s "
                    f"score={bw.composite_score:.3f})[/yellow]"
                )

            clips: list[Path] = []
            if mode_cfg.mode_name == "shortform":
                with clip_progress(_expected_clip_count(mode_cfg, moments)) as on_event:
                    clips = run_render(
                        source, workspace, config, mode_cfg, moments, on_event=on_event
                    )
                console.print(f"  render:    {len(clips)} clip(s) -> src/output/shorts/")
                for clip in clips:
                    console.print(f"    {clip.name}")
            elif mode_cfg.mode_name in ("standard_longform", "cinematic_longform"):
                clips = run_longform_render(source, workspace, config, mode_cfg, moments)
                base = (
                    mode_cfg.model_dump()
                    .get("output", {})
                    .get("base_dir", "src/output/longform/standard")
                )
                console.print(f"  render:    {len(clips)} clip(s) -> {base}/")
                for clip in clips:
                    console.print(f"    {clip.name}")
            else:
                console.print(
                    f"  [yellow]render: {mode_cfg.mode_name} render path not built yet[/yellow]"
                )

            if polish and clips:
                polished = dispatch_polish(mode_cfg, source, clips, console=console)
                for path in polished:
                    console.print(f"    polished:  {path.name}")
    console.print("[green]Done.[/green]")


def _outputs_from_workspace(workspace_dir: Path) -> AnalysisOutputs:
    """Build AnalysisOutputs from artifacts already present in a cached workspace."""
    ws = workspace_from_dir(workspace_dir)
    analysis = ws.analysis_dir

    def opt(rel: str) -> Path | None:
        path = analysis / rel
        return path if path.exists() else None

    transcript = ws.transcript_path if ws.transcript_path.exists() else None
    present = {name for name, rel in _ARTIFACT_FILES.items() if (ws.root / rel).exists()}
    return AnalysisOutputs(
        librosa_path=analysis / "librosa.json",
        opencv_path=opt("opencv.json"),
        aesthetics_path=opt("aesthetics.json"),
        subjects_path=opt("subjects.json"),
        transcript_path=transcript,
        hooks_path=opt("hooks.json"),
        transcribed=transcript is not None,
        from_cache=present,
    )


@app.command()
def reprocess(
    workspace_dir: Path = typer.Argument(..., help="Existing workspace to reprocess."),
    mode: list[str] = typer.Option(..., "--mode", help="Content mode(s)."),
    polish: bool = typer.Option(
        False,
        "--polish",
        help="Hand the rendered longform cut to Descript for polish (longform only).",
    ),
) -> None:
    """Re-run intelligence + render from a cached workspace (skips analysis)."""
    config = load_config()
    setup_logging(config.logging.level, rich_console=config.logging.rich_console)
    if polish and "shortform" in mode:
        console.print(f"[red]{_SHORTFORM_POLISH_ERROR}[/red]")
        raise typer.Exit(1)

    ws = workspace_from_dir(workspace_dir)
    if not ws.metadata_path.exists():
        console.print(
            f"[red]{workspace_dir} is not a processed workspace (no metadata.json). "
            f"Run `cme process` first.[/red]"
        )
        raise typer.Exit(1)

    source = Path(read_json(ws.metadata_path)["path"])
    outputs = _outputs_from_workspace(workspace_dir)
    with keep_awake():
        for mode_name in mode:
            mode_cfg = load_mode_config(mode_name)
            console.print(
                f"[bold]Reprocessing[/bold] {ws.run_id} [dim](mode={mode_cfg.mode_name})[/dim]"
            )
            moments = run_intelligence(source, ws, config, mode_cfg, outputs)
            console.print(f"  moments:   {ws.moments_path} ({moments.moment_count} kept)")
            clips: list[Path] = []
            if mode_cfg.mode_name == "shortform":
                with clip_progress(_expected_clip_count(mode_cfg, moments)) as on_event:
                    clips = run_render(source, ws, config, mode_cfg, moments, on_event=on_event)
                console.print(f"  render:    {len(clips)} clip(s)")
            elif mode_cfg.mode_name in ("standard_longform", "cinematic_longform"):
                clips = run_longform_render(source, ws, config, mode_cfg, moments)
                console.print(f"  render:    {len(clips)} clip(s)")

            if polish and clips:
                polished = dispatch_polish(mode_cfg, source, clips, console=console)
                for path in polished:
                    console.print(f"    polished:  {path.name}")
    console.print("[green]Done.[/green]")


@workspace_app.command("list")
def workspace_list() -> None:
    """List workspaces with their pipeline status (ingested / analyzed / scored)."""
    config = load_config()
    root = config.workspace.root
    if not root.is_dir():
        console.print("[dim]no workspaces[/dim]")
        return
    runs = sorted(
        (d for d in root.iterdir() if d.is_dir() and d.name.startswith("run_")), reverse=True
    )
    if not runs:
        console.print("[dim]no workspaces[/dim]")
        return
    for child in runs:
        ws = workspace_from_dir(child)
        flags = []
        if ws.metadata_path.exists():
            flags.append("ingested")
        if (ws.analysis_dir / "librosa.json").exists():
            flags.append("analyzed")
        if ws.moments_path.exists():
            flags.append("scored")
        status = ", ".join(flags) if flags else "empty"
        console.print(f"  {child.name}  [dim]({status})[/dim]")


@workspace_app.command("info")
def workspace_info(workspace_id: str = typer.Argument(..., help="Workspace run id.")) -> None:
    """Show per-artifact cache status (fresh / stale / uncached) for a workspace."""
    config = load_config()
    ws_dir = config.workspace.root / workspace_id
    if not ws_dir.is_dir():
        console.print(f"[red]no such workspace: {workspace_id}[/red]")
        raise typer.Exit(1)
    ws = workspace_from_dir(ws_dir)
    if not ws.metadata_path.exists():
        console.print("[yellow]workspace has no metadata.json (not ingested)[/yellow]")
        return

    manifest = read_cache_manifest(ws)
    source = Path(read_json(ws.metadata_path)["path"])
    console.print(f"[bold]{workspace_id}[/bold]  source={source}")
    for name, section_attr in _ARTIFACT_SECTIONS.items():
        section = getattr(config, section_attr).model_dump()
        current_fp = artifact_fingerprint(source, section)
        if name not in manifest:
            status = "[dim]uncached[/dim]"
        elif is_artifact_fresh(name, current_fp, manifest):
            status = "[green]fresh[/green]"
        else:
            status = "[red]stale[/red]"
        console.print(f"  {name}: {status}")


@workspace_app.command("clean")
def workspace_clean(
    older_than: str = typer.Option(
        "7d", "--older-than", help="Age threshold, e.g. 7d / 24h / 30m."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List what would be removed; delete nothing."
    ),
) -> None:
    """Clean workspaces older than the threshold (use --dry-run to preview)."""
    config = load_config()
    try:
        age = parse_age(older_than)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    removed = clean_workspaces(config.workspace.root, age, dry_run=dry_run)
    verb = "would remove" if dry_run else "removed"
    for path in removed:
        console.print(f"  {verb}: {path.name}")
    console.print(f"[green]{verb} {len(removed)} workspace(s)[/green]")


@app.command()
def grade(
    source: Path = typer.Argument(..., help="Input video to grade (full file)."),
    output: Path = typer.Argument(..., help="Output graded video path."),
    mode: str = typer.Option("cinematic_longform", "--mode", help="Mode whose LUT to apply."),
    correct: bool = typer.Option(
        True,
        "--correct/--no-correct",
        help="Run technical correction before the LUT (ON by default; per-file [0,duration]).",
    ),
    keep_resolution: bool = typer.Option(
        True,
        "--keep-resolution/--scale-to-mode",
        help="Preserve input resolution (default); --scale-to-mode downscales to the mode size.",
    ),
    hardware: bool = typer.Option(
        True,
        "--hardware/--software",
        help="Encode on the M1 media engine (h264_videotoolbox; default). --software = libx264 CRF.",
    ),
    quality: int = typer.Option(
        18,
        "--quality",
        help="Quality on the CRF scale (lower = better); maps to -q:v on --hardware.",
    ),
    bitrate: str | None = typer.Option(None, "--bitrate", help="Force a fixed bitrate (e.g. 50M)."),
) -> None:
    """Apply a mode's LUT grade to a FULL input file — no selection, no cut."""
    config = load_config()
    setup_logging(config.logging.level, rich_console=config.logging.rich_console)
    mode_cfg = load_mode_config(mode)
    out = run_grade(
        source,
        output,
        config,
        mode_cfg,
        correct=correct,
        scale_to_mode=not keep_resolution,
        quality=quality,
        bitrate=bitrate,
        hardware=hardware,
    )
    console.print(f"[bold]Graded[/bold] {source} -> {out}")


if __name__ == "__main__":
    app()
