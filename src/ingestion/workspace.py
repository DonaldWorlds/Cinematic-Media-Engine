"""Per-run workspace directory management + Phase 6 cache fingerprinting/discovery.

The cache model is two-layer (CP1):
1. Discovery (``find_workspace_for_source``) matches an existing workspace by the
   *resolved* source path recorded in metadata.json.
2. Validation (``artifact_fingerprint`` + the cache manifest) confirms each cached
   artifact is still current. A fast composite key — resolved path + size + mtime +
   a hash of that artifact's relevant config — invalidates on a changed source OR a
   changed config (NOT a full content hash, which is too slow on 4K footage).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..utils.fileio import ensure_dir, write_json
from .metadata import MediaMetadata

CACHE_MANIFEST_NAME = "cache_manifest.json"
SOURCE_KEY = "__source__"  # reserved manifest key: the source identity at ingest time


class Workspace(BaseModel):
    """Paths for a single per-run workspace under ``workspace_root/<run_id>``."""

    run_id: str
    root: Path
    analysis_dir: Path
    output_dir: Path
    audio_path: Path
    transcript_path: Path
    moments_path: Path
    metadata_path: Path


def _source_hash(source: Path) -> str:
    return hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:8]


def workspace_from_dir(root: Path) -> Workspace:
    """Reconstruct a :class:`Workspace` view over an existing run directory."""
    return Workspace(
        run_id=root.name,
        root=root,
        analysis_dir=root / "analysis",
        output_dir=root / "output",
        audio_path=root / "audio.wav",
        transcript_path=root / "transcript.json",
        moments_path=root / "moments.json",
        metadata_path=root / "metadata.json",
    )


def create_workspace(source: Path, workspace_root: Path, *, run_id: str | None = None) -> Workspace:
    """Create ``workspace_root/<run_id>/`` with analysis/ and output/ subdirs."""
    if run_id is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"run_{stamp}_{_source_hash(source)}"
    root = ensure_dir(workspace_root / run_id)
    ensure_dir(root / "analysis")
    ensure_dir(root / "output")
    return workspace_from_dir(root)


def write_metadata(workspace: Workspace, metadata: MediaMetadata) -> Path:
    """Write source metadata to the workspace's metadata.json; return its path."""
    write_json(workspace.metadata_path, metadata.model_dump())
    return workspace.metadata_path


# --------------------------------------------------------------------------- #
# Phase 6 — cache fingerprinting
# --------------------------------------------------------------------------- #


def source_identity(source: Path) -> str:
    """Fast identity key for a source file: resolved path + size + mtime (no content hash)."""
    st = source.stat()
    return f"{source.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def _config_hash(config_section: Mapping[str, Any]) -> str:
    payload = json.dumps(config_section, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def artifact_fingerprint(source: Path, config_section: Mapping[str, Any]) -> str:
    """Composite cache key for one artifact: source identity + its relevant config.

    Deterministic for unchanged inputs; changes when the source file changes (size or
    mtime) OR when that artifact's config section changes. Per-artifact: passing a
    different config section yields an independent fingerprint, so changing one signal's
    config never invalidates another's.
    """
    payload = f"{source_identity(source)}|{_config_hash(config_section)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Phase 6 — cache manifest (per-workspace artifact-name -> fingerprint)
# --------------------------------------------------------------------------- #


def _manifest_path(workspace: Workspace) -> Path:
    return workspace.root / CACHE_MANIFEST_NAME


def read_cache_manifest(workspace: Workspace) -> dict[str, str]:
    """Read the cache manifest; return {} on a missing or malformed file (never raise)."""
    path = _manifest_path(workspace)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def write_cache_manifest(workspace: Workspace, manifest: Mapping[str, str]) -> None:
    """Atomically write the cache manifest (temp file + os.replace, no partial writes)."""
    path = _manifest_path(workspace)
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(manifest), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def is_artifact_fresh(name: str, current_fp: str, manifest: Mapping[str, str]) -> bool:
    """True when the manifest records ``current_fp`` for ``name`` (else stale/absent)."""
    return manifest.get(name) == current_fp


def source_unchanged(workspace: Workspace, source: Path) -> bool:
    """True when ``source``'s identity matches the one recorded at ingest time."""
    return read_cache_manifest(workspace).get(SOURCE_KEY) == source_identity(source)


# --------------------------------------------------------------------------- #
# Phase 6 — workspace discovery + cleanup
# --------------------------------------------------------------------------- #


def find_workspace_for_source(source: Path, workspace_root: Path) -> Workspace | None:
    """Return the newest workspace whose metadata records ``source`` (resolved), or None."""
    root = Path(workspace_root)
    if not root.is_dir():
        return None
    target = source.resolve()
    matches: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = child / "metadata.json"
        if not meta.exists():
            continue
        try:
            recorded = Path(str(json.loads(meta.read_text(encoding="utf-8"))["path"])).resolve()
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, KeyError, TypeError):
            continue  # corrupted/incomplete metadata -> skip, don't crash discovery
        if recorded == target:
            matches.append(child)
    if not matches:
        return None
    return workspace_from_dir(max(matches, key=lambda d: d.stat().st_mtime))


def parse_age(value: str) -> timedelta:
    """Parse an age string like ``7d`` / ``24h`` / ``30m`` into a timedelta."""
    match = re.fullmatch(r"\s*(\d+)\s*([dhm])\s*", value)
    if match is None:
        raise ValueError(f"invalid age '{value}'; use e.g. '7d', '24h', or '30m'")
    n = int(match.group(1))
    unit = match.group(2)
    return {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]


def clean_workspaces(
    workspace_root: Path,
    older_than: timedelta,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[Path]:
    """Remove ``run_*`` workspaces older than ``older_than`` by mtime; return what was removed.

    Recency protects active/in-progress runs (their mtime is fresh, so they never become
    candidates). ``dry_run`` reports candidates without deleting.
    """
    root = Path(workspace_root)
    if not root.is_dir():
        return []
    cutoff = (now or datetime.now()).timestamp() - older_than.total_seconds()
    removed: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not child.name.startswith("run_"):
            continue
        if child.stat().st_mtime >= cutoff:
            continue  # recent -> keep (protects active runs)
        removed.append(child)
        if not dry_run:
            shutil.rmtree(child)
    return removed
