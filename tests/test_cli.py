"""Phase 6 — CLI tests: reprocess, --fresh, workspace subcommands, caffeinate guard.

Written before implementation. `reprocess` must NOT re-run analysis; the workspace
subcommands report status / staleness / clean old runs; the caffeinate guard spawns
only on macOS and is a no-op elsewhere.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from typer.testing import CliRunner

runner = CliRunner()


def _minimal_moments(source: Path):
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )

    return MomentsResult(
        run_id="run_x",
        mode="shortform",
        source=str(source),
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
        moments=[
            Moment(
                rank=1,
                start_sec=0.0,
                end_sec=60.0,
                duration_sec=60.0,
                composite_score=0.7,
                scene_snapped=False,
                signal_contributions={},
            )
        ],
        moment_count=1,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )


def _seed_workspace(tmp_path: Path, run_id: str):
    from src.ingestion.metadata import MediaMetadata
    from src.ingestion.workspace import create_workspace, write_metadata

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id=run_id)
    meta = MediaMetadata(
        path=str(source),
        size_bytes=1,
        duration_sec=120.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )
    write_metadata(ws, meta)
    return source, ws


# --------------------------------------------------------------------------- #
# reprocess — analysis must NOT run
# --------------------------------------------------------------------------- #


def test_reprocess_skips_analysis(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_a")
    called = {"analysis": False}

    def no_analysis(*a, **k):
        called["analysis"] = True
        raise AssertionError("analysis must not run during reprocess")

    monkeypatch.setattr(cli, "run_analysis", no_analysis, raising=False)
    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(cli, "run_render", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["reprocess", str(ws.root), "--mode", "shortform"])
    assert result.exit_code == 0, result.output
    assert called["analysis"] is False


def test_reprocess_errors_on_missing_artifacts(tmp_path: Path) -> None:
    from src import cli

    empty = tmp_path / "empty_ws"
    empty.mkdir()
    result = runner.invoke(cli.app, ["reprocess", str(empty), "--mode", "shortform"])
    assert result.exit_code != 0
    assert "traceback" not in result.output.lower()


def test_process_fresh_flag_plumbed(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    captured = {}

    def fake_analysis(*a, **k):
        captured["fresh"] = k.get("fresh")
        raise SystemExit(0)  # short-circuit after capturing

    monkeypatch.setattr(cli, "run_analysis", fake_analysis)
    monkeypatch.setattr(cli, "ingest", lambda *a, **k: _seed_workspace(tmp_path, "run_f")[1])
    runner.invoke(cli.app, ["process", str(tmp_path / "vid.mp4"), "--mode", "shortform", "--fresh"])
    assert captured.get("fresh") is True


# --------------------------------------------------------------------------- #
# workspace list / info / clean
# --------------------------------------------------------------------------- #


def _config_at(tmp_root: Path):
    from src.config import load_config

    cfg = load_config()
    cfg.workspace.root = tmp_root
    return cfg


def test_workspace_list_reports_status(tmp_path: Path, monkeypatch) -> None:
    from src import cli
    from src.utils.fileio import write_json

    _, ws_scored = _seed_workspace(tmp_path, "run_scored")
    write_json(ws_scored.moments_path, {"moment_count": 1})
    monkeypatch.setattr(cli, "load_config", lambda: _config_at(ws_scored.root.parent))

    result = runner.invoke(cli.app, ["workspace", "list"])
    assert result.exit_code == 0, result.output
    assert "run_scored" in result.output
    assert "scored" in result.output.lower()


def test_workspace_info_marks_stale(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    _, ws = _seed_workspace(tmp_path, "run_info")
    # record a cache manifest, then mutate the relevant config so it is stale
    from src.ingestion.workspace import write_cache_manifest

    write_cache_manifest(ws, {"librosa": "OLD_FINGERPRINT"})
    monkeypatch.setattr(cli, "load_config", lambda: _config_at(ws.root.parent))

    result = runner.invoke(cli.app, ["workspace", "info", ws.run_id])
    assert result.exit_code == 0, result.output
    assert "stale" in result.output.lower()


def test_workspace_clean_removes_only_old(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    root = tmp_path / "workspace"
    root.mkdir()
    old = root / "run_old"
    new = root / "run_new"
    old.mkdir()
    new.mkdir()
    old_time = time.time() - 30 * 86400
    os.utime(old, (old_time, old_time))
    monkeypatch.setattr(cli, "load_config", lambda: _config_at(root))

    result = runner.invoke(cli.app, ["workspace", "clean", "--older-than", "7d"])
    assert result.exit_code == 0, result.output
    assert not old.exists()
    assert new.exists()


def test_workspace_clean_dry_run_removes_nothing(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    root = tmp_path / "workspace"
    root.mkdir()
    old = root / "run_old"
    old.mkdir()
    old_time = time.time() - 30 * 86400
    os.utime(old, (old_time, old_time))
    monkeypatch.setattr(cli, "load_config", lambda: _config_at(root))

    result = runner.invoke(cli.app, ["workspace", "clean", "--older-than", "7d", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert old.exists()  # dry-run must not delete
    assert "run_old" in result.output


# --------------------------------------------------------------------------- #
# caffeinate guard (keep_awake)
# --------------------------------------------------------------------------- #


def test_keep_awake_spawns_caffeinate_on_macos(monkeypatch) -> None:
    import src.utils.keepawake as ka

    calls: dict[str, list[str]] = {}

    class _FakeProc:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    fake = _FakeProc()

    def fake_popen(cmd, *a, **k):
        calls["cmd"] = cmd
        return fake

    monkeypatch.setattr(ka.sys, "platform", "darwin")
    monkeypatch.setattr(ka.subprocess, "Popen", fake_popen)
    with ka.keep_awake():
        pass
    assert calls["cmd"][0] == "caffeinate"
    assert "-w" in calls["cmd"]
    assert fake.terminated is True


def test_keep_awake_noop_off_macos(monkeypatch) -> None:
    import src.utils.keepawake as ka

    def boom(*a, **k):
        raise AssertionError("Popen must not be called off macOS")

    monkeypatch.setattr(ka.sys, "platform", "linux")
    monkeypatch.setattr(ka.subprocess, "Popen", boom)
    with ka.keep_awake():
        pass  # no exception == pass


# --------------------------------------------------------------------------- #
# Phase 7 — standard_longform dispatch (process + reprocess route to the
# isolated longform path; shortform still routes to run_render)
# --------------------------------------------------------------------------- #


def _fake_analysis_outputs():
    from types import SimpleNamespace

    return SimpleNamespace(from_cache=set(), transcribed=False)


def test_process_standard_longform_dispatches_longform(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_lf")
    called = {"longform": False, "shortform": False}

    monkeypatch.setattr(cli, "ingest", lambda *a, **k: ws)
    monkeypatch.setattr(cli, "run_analysis", lambda *a, **k: _fake_analysis_outputs())
    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(
        cli, "run_render", lambda *a, **k: called.__setitem__("shortform", True) or []
    )
    monkeypatch.setattr(
        cli,
        "run_longform_render",
        lambda *a, **k: called.__setitem__("longform", True) or [],
        raising=False,
    )

    result = runner.invoke(cli.app, ["process", str(source), "--mode", "standard_longform"])
    assert result.exit_code == 0, result.output
    assert called["longform"] is True
    assert called["shortform"] is False


def test_process_standard_longform_no_notbuilt_notice(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_lf2")
    monkeypatch.setattr(cli, "ingest", lambda *a, **k: ws)
    monkeypatch.setattr(cli, "run_analysis", lambda *a, **k: _fake_analysis_outputs())
    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(cli, "run_longform_render", lambda *a, **k: [], raising=False)

    result = runner.invoke(cli.app, ["process", str(source), "--mode", "standard_longform"])
    assert "not built" not in result.output.lower()


def test_process_shortform_still_dispatches_shortform(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_sf")
    called = {"longform": False, "shortform": False}

    monkeypatch.setattr(cli, "ingest", lambda *a, **k: ws)
    monkeypatch.setattr(cli, "run_analysis", lambda *a, **k: _fake_analysis_outputs())
    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(
        cli, "run_render", lambda *a, **k: called.__setitem__("shortform", True) or []
    )
    monkeypatch.setattr(
        cli,
        "run_longform_render",
        lambda *a, **k: called.__setitem__("longform", True) or [],
        raising=False,
    )

    result = runner.invoke(cli.app, ["process", str(source), "--mode", "shortform"])
    assert result.exit_code == 0, result.output
    assert called["shortform"] is True
    assert called["longform"] is False


# --------------------------------------------------------------------------- #
# Phase 8 — cinematic_longform dispatch: routes to the SAME longform path as
# standard_longform (LUT is read from mode config inside run_longform_render),
# replacing the Phase 7 "not built" notice. standard/shortform unchanged.
# --------------------------------------------------------------------------- #


def test_process_cinematic_longform_dispatches_longform(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_cine")
    called = {"longform": False, "shortform": False}

    monkeypatch.setattr(cli, "ingest", lambda *a, **k: ws)
    monkeypatch.setattr(cli, "run_analysis", lambda *a, **k: _fake_analysis_outputs())
    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(
        cli, "run_render", lambda *a, **k: called.__setitem__("shortform", True) or []
    )
    monkeypatch.setattr(
        cli,
        "run_longform_render",
        lambda *a, **k: called.__setitem__("longform", True) or [],
        raising=False,
    )

    result = runner.invoke(cli.app, ["process", str(source), "--mode", "cinematic_longform"])
    assert result.exit_code == 0, result.output
    assert called["longform"] is True
    assert called["shortform"] is False


def test_process_cinematic_longform_no_notbuilt_notice(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_cine2")
    monkeypatch.setattr(cli, "ingest", lambda *a, **k: ws)
    monkeypatch.setattr(cli, "run_analysis", lambda *a, **k: _fake_analysis_outputs())
    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(cli, "run_longform_render", lambda *a, **k: [], raising=False)

    result = runner.invoke(cli.app, ["process", str(source), "--mode", "cinematic_longform"])
    assert "not built" not in result.output.lower()


def test_reprocess_cinematic_longform_dispatches_longform(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_cine3")
    called = {"longform": False}

    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(
        cli,
        "run_longform_render",
        lambda *a, **k: called.__setitem__("longform", True) or [],
        raising=False,
    )

    result = runner.invoke(cli.app, ["reprocess", str(ws.root), "--mode", "cinematic_longform"])
    assert result.exit_code == 0, result.output
    assert called["longform"] is True


def test_reprocess_standard_longform_dispatches_longform(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    source, ws = _seed_workspace(tmp_path, "run_lfr")
    called = {"longform": False}

    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _minimal_moments(source))
    monkeypatch.setattr(cli, "_outputs_from_workspace", lambda *a, **k: _fake_analysis_outputs())
    monkeypatch.setattr(
        cli,
        "run_longform_render",
        lambda *a, **k: called.__setitem__("longform", True) or [],
        raising=False,
    )

    result = runner.invoke(cli.app, ["reprocess", str(ws.root), "--mode", "standard_longform"])
    assert result.exit_code == 0, result.output
    assert called["longform"] is True


# --------------------------------------------------------------------------- #
# `cme grade` — standalone full-file LUT grade (no selection/cut). Written before
# implementation: the `grade` command + cli.run_grade do not exist yet (RED).
# --------------------------------------------------------------------------- #


def test_grade_command_dispatches_run_grade(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    captured: dict = {}
    monkeypatch.setattr(
        cli,
        "run_grade",
        lambda source, output, config, mode, **kw: (
            captured.update(source=source, output=output, mode=mode.mode_name, kw=kw) or output
        ),
        raising=False,
    )

    result = runner.invoke(cli.app, ["grade", str(src), str(out)])
    assert result.exit_code == 0, result.output
    assert captured["source"] == src and captured["output"] == out
    assert captured["mode"] == "cinematic_longform"  # default mode
    assert captured["kw"]["correct"] is True  # correction ON by default
    assert captured["kw"]["scale_to_mode"] is False  # native resolution by default
    assert captured["kw"]["codec"] == "prores"  # ProRes 422 HQ master by default
    # h264-only knobs are not forwarded for the prores default
    assert captured["kw"].get("quality") is None
    assert captured["kw"].get("bitrate") is None
    assert captured["kw"].get("hardware") is None


def test_grade_command_flags_flow_through(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    captured: dict = {}
    monkeypatch.setattr(
        cli,
        "run_grade",
        lambda source, output, config, mode, **kw: captured.update(kw=kw) or output,
        raising=False,
    )

    result = runner.invoke(
        cli.app,
        [
            "grade",
            str(src),
            str(tmp_path / "o.mp4"),
            "--codec",
            "h264",
            "--no-correct",
            "--scale-to-mode",
            "--quality",
            "16",
            "--bitrate",
            "50M",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["kw"]["codec"] == "h264"
    assert captured["kw"]["correct"] is False
    assert captured["kw"]["scale_to_mode"] is True
    assert captured["kw"]["quality"] == 16
    assert captured["kw"]["bitrate"] == "50M"


def test_grade_command_software_flag(tmp_path: Path, monkeypatch) -> None:
    from src import cli

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    captured: dict = {}
    monkeypatch.setattr(
        cli,
        "run_grade",
        lambda source, output, config, mode, **kw: captured.update(kw=kw) or output,
        raising=False,
    )

    result = runner.invoke(
        cli.app, ["grade", str(src), str(tmp_path / "o.mp4"), "--codec", "h264", "--software"]
    )
    assert result.exit_code == 0, result.output
    assert captured["kw"]["codec"] == "h264"
    assert captured["kw"]["hardware"] is False  # --software disables hardware encode (h264 only)


def test_grade_command_prores_rejects_h264_knobs(tmp_path: Path, monkeypatch) -> None:
    """Fail loud: h264 knobs are invalid with the prores default; never reach the encode."""
    from src import cli

    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    called = {"n": 0}
    monkeypatch.setattr(
        cli, "run_grade", lambda *a, **k: called.__setitem__("n", called["n"] + 1), raising=False
    )

    result = runner.invoke(cli.app, ["grade", str(src), str(tmp_path / "o.mov"), "--quality", "16"])
    assert result.exit_code != 0  # rejected before any work
    assert called["n"] == 0  # run_grade / the encode never reached
    assert "prores" in result.output.lower() or "codec" in result.output.lower()
