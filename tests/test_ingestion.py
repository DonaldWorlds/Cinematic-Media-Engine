"""Phase 1 ingestion + config tests (written before implementation).

Covers: utils (timestamps, fileio), config loading + validation, and the
ingestion layer (metadata, validator, workspace, audio_extract) plus the
end-to-end ingest() pipeline that backs `cme process`.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import pytest

from src.config import Config, ModeConfig, load_config, load_mode_config
from src.ingestion import ingest
from src.ingestion.audio_extract import extract_audio
from src.ingestion.metadata import MediaMetadata, extract_metadata
from src.ingestion.validator import InputValidationError, validate_input
from src.ingestion.workspace import Workspace, create_workspace, write_metadata
from src.utils import fileio, timestamps

# --------------------------------------------------------------------------- #
# utils/timestamps
# --------------------------------------------------------------------------- #


def test_seconds_to_hms_formats_with_milliseconds() -> None:
    assert timestamps.seconds_to_hms(3661.5) == "01:01:01.500"


def test_seconds_to_hms_zero() -> None:
    assert timestamps.seconds_to_hms(0.0) == "00:00:00.000"


def test_hms_to_seconds_roundtrip() -> None:
    for value in (0.0, 1.234, 59.999, 3661.5, 7200.0):
        out = timestamps.hms_to_seconds(timestamps.seconds_to_hms(value))
        assert math.isclose(out, value, abs_tol=0.001)


# --------------------------------------------------------------------------- #
# utils/fileio
# --------------------------------------------------------------------------- #


def test_write_json_creates_parent_and_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "data.json"
    payload = {"a": 1, "b": [1, 2, 3], "c": "x"}
    fileio.write_json(target, payload)
    assert target.exists()
    assert fileio.read_json(target) == payload


def test_ensure_dir_creates_nested(tmp_path: Path) -> None:
    d = tmp_path / "x" / "y" / "z"
    out = fileio.ensure_dir(d)
    assert out == d
    assert d.is_dir()


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


def test_load_config_returns_config() -> None:
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.transcription.backend == "whisperx"
    assert cfg.transcription.language == "en"
    assert cfg.audio.sample_rate == 16000


def test_load_mode_config_shortform() -> None:
    mode = load_mode_config("shortform")
    assert isinstance(mode, ModeConfig)
    assert mode.mode_name == "shortform"
    assert mode.transcription.enabled is True


def test_transcription_mode_gating() -> None:
    assert load_mode_config("shortform").transcription.enabled is True
    assert load_mode_config("cinematic_longform").transcription.enabled is False
    assert load_mode_config("standard_longform").transcription.enabled is False
    assert load_mode_config("standard_longform").transcription.gates_srt is True


@pytest.mark.parametrize("mode_name", ["shortform", "cinematic_longform", "standard_longform"])
def test_mode_scoring_weights_sum_to_one(mode_name: str) -> None:
    mode = load_mode_config(mode_name)
    assert math.isclose(sum(mode.scoring_weights.values()), 1.0, abs_tol=1e-9)


def test_load_mode_config_invalid_raises() -> None:
    with pytest.raises(ValueError):
        load_mode_config("does_not_exist")


# --------------------------------------------------------------------------- #
# ingestion/metadata
# --------------------------------------------------------------------------- #


def test_extract_metadata_reads_streams(sample_video: Path) -> None:
    meta = extract_metadata(sample_video)
    assert isinstance(meta, MediaMetadata)
    assert meta.has_video is True
    assert meta.has_audio is True
    assert meta.width == 1920
    assert meta.height == 1080
    assert math.isclose(meta.duration_sec, 60.0, abs_tol=1.0)
    assert math.isclose(meta.fps, 30.0, abs_tol=0.5)
    assert meta.size_bytes > 0


# --------------------------------------------------------------------------- #
# ingestion/validator
# --------------------------------------------------------------------------- #


def test_validate_accepts_valid_video(sample_video: Path) -> None:
    meta = validate_input(sample_video)
    assert isinstance(meta, MediaMetadata)
    assert meta.has_video is True


def test_validate_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InputValidationError):
        validate_input(tmp_path / "nope.mp4")


def test_validate_rejects_non_video(non_video_file: Path) -> None:
    with pytest.raises(InputValidationError):
        validate_input(non_video_file)


# --------------------------------------------------------------------------- #
# ingestion/workspace
# --------------------------------------------------------------------------- #


def test_create_workspace_makes_dirs(tmp_path: Path, sample_video: Path) -> None:
    ws = create_workspace(sample_video, tmp_path / "workspace")
    assert isinstance(ws, Workspace)
    assert ws.root.is_dir()
    assert ws.analysis_dir.is_dir()
    assert ws.output_dir.is_dir()
    assert ws.audio_path.parent == ws.root


def test_create_workspace_run_ids_unique(tmp_path: Path, sample_video: Path) -> None:
    a = create_workspace(sample_video, tmp_path / "ws", run_id="run_a")
    b = create_workspace(sample_video, tmp_path / "ws", run_id="run_b")
    assert a.root != b.root


def test_write_metadata_roundtrips(tmp_path: Path, sample_video: Path) -> None:
    ws = create_workspace(sample_video, tmp_path / "workspace")
    meta = extract_metadata(sample_video)
    out = write_metadata(ws, meta)
    assert out == ws.metadata_path
    assert ws.metadata_path.exists()
    loaded = fileio.read_json(ws.metadata_path)
    assert loaded["width"] == 1920


# --------------------------------------------------------------------------- #
# ingestion/audio_extract
# --------------------------------------------------------------------------- #


def test_extract_audio_produces_16k_mono_wav(tmp_path: Path, sample_video: Path) -> None:
    dest = tmp_path / "audio.wav"
    out = extract_audio(sample_video, dest)
    assert out == dest
    assert dest.exists()
    with wave.open(str(dest), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        duration = w.getnframes() / w.getframerate()
    assert math.isclose(duration, 60.0, abs_tol=1.5)


# --------------------------------------------------------------------------- #
# end-to-end ingest() — backs `cme process`
# --------------------------------------------------------------------------- #


def test_ingest_pipeline_end_to_end(tmp_path: Path, sample_video: Path) -> None:
    cfg = load_config()
    cfg.workspace.root = tmp_path / "workspace"
    ws = ingest(sample_video, cfg)
    assert ws.root.is_dir()
    assert ws.audio_path.exists()
    assert ws.metadata_path.exists()
    meta = fileio.read_json(ws.metadata_path)
    assert meta["has_audio"] is True


# --------------------------------------------------------------------------- #
# Phase 6 — cache fingerprint + staleness model (written before implementation)
# --------------------------------------------------------------------------- #


def _src(tmp_path: Path, content: bytes = b"abc") -> Path:
    p = tmp_path / "src.mp4"
    p.write_bytes(content)
    return p


def test_fingerprint_stable_for_unchanged_inputs(tmp_path: Path) -> None:
    from src.ingestion.workspace import artifact_fingerprint

    src = _src(tmp_path)
    cfg = {"sample_rate": 16000}
    assert artifact_fingerprint(src, cfg) == artifact_fingerprint(src, cfg)


def test_fingerprint_changes_on_source_size(tmp_path: Path) -> None:
    from src.ingestion.workspace import artifact_fingerprint

    src = _src(tmp_path, b"abc")
    cfg = {"sample_rate": 16000}
    before = artifact_fingerprint(src, cfg)
    src.write_bytes(b"abcdefghij")  # size change
    assert artifact_fingerprint(src, cfg) != before


def test_fingerprint_changes_on_mtime(tmp_path: Path) -> None:
    import os

    from src.ingestion.workspace import artifact_fingerprint

    src = _src(tmp_path)
    cfg = {"sample_rate": 16000}
    before = artifact_fingerprint(src, cfg)
    future = src.stat().st_mtime + 1000
    os.utime(src, (future, future))
    assert artifact_fingerprint(src, cfg) != before


def test_fingerprint_changes_on_relevant_config(tmp_path: Path) -> None:
    from src.ingestion.workspace import artifact_fingerprint

    src = _src(tmp_path)
    before = artifact_fingerprint(src, {"sample_rate": 16000})
    assert artifact_fingerprint(src, {"sample_rate": 22050}) != before


def test_per_artifact_config_isolation(tmp_path: Path) -> None:
    from src.ingestion.workspace import artifact_fingerprint

    src = _src(tmp_path)
    audio_cfg = {"sample_rate": 16000}
    opencv_cfg = {"scene_detect_threshold": 27.0}
    lib1 = artifact_fingerprint(src, audio_cfg)
    op1 = artifact_fingerprint(src, opencv_cfg)
    # mutate ONLY the opencv-relevant config
    lib2 = artifact_fingerprint(src, audio_cfg)
    op2 = artifact_fingerprint(src, {"scene_detect_threshold": 40.0})
    assert lib1 == lib2  # librosa fingerprint unaffected by opencv config change
    assert op1 != op2


def test_is_artifact_fresh(tmp_path: Path) -> None:
    from src.ingestion.workspace import is_artifact_fresh

    manifest = {"librosa": "FP1"}
    assert is_artifact_fresh("librosa", "FP1", manifest) is True
    assert is_artifact_fresh("librosa", "FP2", manifest) is False
    assert is_artifact_fresh("opencv", "FP1", manifest) is False  # not in manifest


def test_cache_manifest_roundtrips(tmp_path: Path, sample_video: Path) -> None:
    from src.ingestion.workspace import (
        create_workspace,
        read_cache_manifest,
        write_cache_manifest,
    )

    ws = create_workspace(sample_video, tmp_path / "ws", run_id="run_m")
    write_cache_manifest(ws, {"librosa": "FP1", "opencv": "FP2"})
    assert read_cache_manifest(ws) == {"librosa": "FP1", "opencv": "FP2"}


def test_find_workspace_for_source_returns_newest_match(tmp_path: Path) -> None:
    import os

    from src.ingestion.metadata import MediaMetadata
    from src.ingestion.workspace import create_workspace, find_workspace_for_source, write_metadata

    root = tmp_path / "ws"
    src_a = tmp_path / "a.mp4"
    src_a.write_bytes(b"a")
    src_b = tmp_path / "b.mp4"
    src_b.write_bytes(b"b")

    def meta_for(p: Path) -> MediaMetadata:
        return MediaMetadata(
            path=str(p.resolve()),
            duration_sec=1.0,
            width=2,
            height=2,
            fps=1.0,
            video_codec="h264",
            audio_codec="aac",
            has_video=True,
            has_audio=True,
            size_bytes=1,
        )

    a1 = create_workspace(src_a, root, run_id="run_a1")
    write_metadata(a1, meta_for(src_a))
    a2 = create_workspace(src_a, root, run_id="run_a2")
    write_metadata(a2, meta_for(src_a))
    b1 = create_workspace(src_b, root, run_id="run_b1")
    write_metadata(b1, meta_for(src_b))

    # make a2 the newest workspace for source A
    older = a1.root.stat().st_mtime
    os.utime(a1.root, (older, older - 100))
    os.utime(a2.root, (older, older + 100))

    found = find_workspace_for_source(src_a, root)
    assert found is not None
    assert found.run_id == "run_a2"
    assert find_workspace_for_source(tmp_path / "unseen.mp4", root) is None


def test_parse_age(tmp_path: Path) -> None:
    from datetime import timedelta

    from src.ingestion.workspace import parse_age

    assert parse_age("7d") == timedelta(days=7)
    assert parse_age("24h") == timedelta(hours=24)
    with pytest.raises(ValueError):
        parse_age("banana")
