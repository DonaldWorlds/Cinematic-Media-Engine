"""Ingestion layer: validate -> metadata -> workspace -> audio extraction.

Phase 6: ``ingest`` is cache-aware. It reuses an existing workspace when one is found
for the (unchanged) source, and otherwise creates a fresh one — logging the decision
loudly. ``--fresh`` forces a new workspace. Source-level reuse is keyed on identity
(path + size + mtime); per-artifact config staleness is handled later in run_analysis.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..utils.logging import get_logger
from .audio_extract import extract_audio
from .validator import validate_input
from .workspace import (
    SOURCE_KEY,
    Workspace,
    create_workspace,
    find_workspace_for_source,
    read_cache_manifest,
    source_identity,
    source_unchanged,
    write_cache_manifest,
    write_metadata,
)

__all__ = ["Workspace", "ingest"]

_log = get_logger(__name__)


def ingest(source: Path, config: Config, *, fresh: bool = False) -> Workspace:
    """Run the Phase 1 ingestion pipeline (cache-aware) and return the workspace.

    Reuse an existing workspace when the source is unchanged; otherwise validate the
    input, create a per-run workspace, extract analysis audio, and persist metadata +
    the source identity. No analysis or rendering happens here.
    """
    source = source.resolve()

    if not fresh:
        existing = find_workspace_for_source(source, config.workspace.root)
        if existing is not None and source_unchanged(existing, source):
            _log.info("ingest: reusing cached workspace %s (source unchanged)", existing.run_id)
            return existing
        reason = "source changed" if existing is not None else "no cached workspace for source"
    else:
        reason = "--fresh requested"
    _log.info("ingest: creating fresh workspace (reason: %s)", reason)

    metadata = validate_input(source, ffprobe_binary=config.ffmpeg.ffprobe_path)
    workspace = create_workspace(source, config.workspace.root)
    extract_audio(
        source,
        workspace.audio_path,
        sample_rate=config.audio.sample_rate,
        ffmpeg_binary=config.ffmpeg.binary_path,
    )
    write_metadata(workspace, metadata)

    manifest = read_cache_manifest(workspace)
    manifest[SOURCE_KEY] = source_identity(source)
    write_cache_manifest(workspace, manifest)
    return workspace
