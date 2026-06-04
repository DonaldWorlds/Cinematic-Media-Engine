"""Phase 10 — Professional Color Pipeline (technical correction stage).

Window-scoped exposure / white-balance / contrast correction, prepended BEFORE the
Phase-8 ``lut3d`` grade in ``build_longform_command``. Auto-detect with per-mode config
override; cinematic_longform REQUIRES it, standard_longform leaves it OFF by default,
shortform is N/A. Analysis is fail-loud on an unopenable/unsamplable source and the
computed correction is cached in the Phase-6 workspace.

Imports are function-local (matching the repo convention) so a missing symbol fails ONE
test with a clear reason rather than breaking module collection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Fixtures: solid warm / cool clips for end-to-end white-balance detection.
# Generated on demand via ffmpeg lavfi, same pattern as conftest's sample_video.
# Mild (non-primary) colors so no channel mean is ~0 (clean gray-world gains).
# --------------------------------------------------------------------------- #


def _gen_solid(dest: Path, color: str) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:size=320x240:rate=10:duration=2",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"solid-clip ffmpeg failed: {result.stderr[-800:]}")


@pytest.fixture(scope="session")
def warm_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 2s solid warm clip (R>G>B) — red gain should drop, blue gain should rise."""
    p = tmp_path_factory.mktemp("colorclips") / "warm.mp4"
    _gen_solid(p, "0xCC8844")
    return p


@pytest.fixture(scope="session")
def cool_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 2s solid cool clip (B>G>R) — blue gain should drop, red gain should rise."""
    p = tmp_path_factory.mktemp("colorclips") / "cool.mp4"
    _gen_solid(p, "0x4488CC")
    return p


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _cp_config(
    *,
    enabled: bool = True,
    exposure_mode: str = "auto",
    exposure_override: float | None = None,
    wb_mode: str = "auto",
    wb_kelvin: int | None = None,
    contrast_mode: str = "auto",
    contrast_curve: str | None = None,
):
    from src.config import (
        ColorPipelineConfig,
        ContrastSettings,
        ExposureSettings,
        TechnicalCorrectionSettings,
        WhiteBalanceSettings,
    )

    return ColorPipelineConfig(
        technical_correction=TechnicalCorrectionSettings(
            enabled=enabled,
            exposure=ExposureSettings(mode=exposure_mode, override_value=exposure_override),
            white_balance=WhiteBalanceSettings(mode=wb_mode, override_kelvin=wb_kelvin),
            contrast=ContrastSettings(mode=contrast_mode, override_curve=contrast_curve),
        )
    )


class _RecordingLongformBackend:
    """Captures the rendered spec without invoking ffmpeg (duck-typed backend)."""

    def __init__(self) -> None:
        self.specs: list = []

    def render_clip(self, spec):
        self.specs.append(spec)
        return spec.output_path


def _moments(source: Path, start: float, end: float, *, mode: str = "cinematic_longform"):
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )

    moment = Moment(
        rank=1,
        start_sec=start,
        end_sec=end,
        duration_sec=end - start,
        composite_score=0.6,
        scene_snapped=False,
        signal_contributions={},
    )
    return MomentsResult(
        run_id="run_cp",
        mode=mode,
        source=str(source),
        generated_at="t",
        source_duration_sec=900.0,
        window_sec=end - start,
        composite_method="weighted_sum",
        score_threshold=0.4,
        diversity=DiversityReport(min_gap_sec=15.0, max_clips_per_minute=None),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=start,
            end_sec=end,
            composite_score=0.6,
            signal_contributions={},
            signal_values={},
        ),
        moments=[moment],
        moment_count=1,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )


def _cinematic_mode():
    """Real cinematic_longform config, LUT swapped to the committed identity fixture."""
    from src.config import ModeConfig, load_mode_config

    dump = load_mode_config("cinematic_longform").model_dump()
    dump["color_grade"] = {
        "enabled": True,
        "lut_path": "tests/fixtures/identity.cube",
        "interp": "tetrahedral",
    }
    return ModeConfig(**dump)


def _standard_mode():
    from src.config import load_mode_config

    return load_mode_config("standard_longform")


# =========================================================================== #
# Group A — pure helpers (fast, deterministic, no video)
# =========================================================================== #


def test_window_sample_times_within_window_and_evenly_distributed() -> None:
    from src.render.color_pipeline import window_sample_times

    t = window_sample_times(10.0, 40.0, 30, duration=60.0)
    assert len(t) == 30
    assert all(10.0 <= x <= 40.0 for x in t)
    assert all(t[i] < t[i + 1] for i in range(len(t) - 1))  # strictly increasing
    gaps = [t[i + 1] - t[i] for i in range(len(t) - 1)]
    assert max(gaps) - min(gaps) < 1e-6  # evenly distributed


def test_window_sample_times_clamps_to_duration() -> None:
    from src.render.color_pipeline import window_sample_times

    t = window_sample_times(60.0, 360.0, 30, duration=254.0)
    assert all(60.0 <= x <= 254.0 for x in t)
    assert max(t) <= 254.0
    with pytest.raises(ValueError):
        window_sample_times(300.0, 360.0, 30, duration=254.0)  # window past EOF -> empty


def test_exposure_adjustment_targets_midgray() -> None:
    from src.render.color_pipeline import exposure_adjustment

    assert exposure_adjustment(0.5) == pytest.approx(0.0)
    assert exposure_adjustment(0.25) == pytest.approx(0.25)
    assert exposure_adjustment(0.9) == pytest.approx(-0.4)
    assert exposure_adjustment(0.0) == pytest.approx(0.5)
    v = exposure_adjustment(0.5)
    assert isinstance(v, float) and -1.0 <= v <= 1.0


def test_gray_world_gains_neutral_input_returns_unity() -> None:
    from src.render.color_pipeline import gray_world_gains

    g = gray_world_gains((120.0, 120.0, 120.0))
    assert g["red_gain"] == pytest.approx(1.0)
    assert g["green_gain"] == pytest.approx(1.0)
    assert g["blue_gain"] == pytest.approx(1.0)


def test_gray_world_gains_detect_warm() -> None:
    from src.render.color_pipeline import gray_world_gains

    g = gray_world_gains((204.0, 136.0, 68.0))
    assert g["red_gain"] < 1.0 < g["blue_gain"]
    assert g["green_gain"] == pytest.approx(1.0)


def test_gray_world_gains_detect_cool() -> None:
    from src.render.color_pipeline import gray_world_gains

    g = gray_world_gains((68.0, 136.0, 204.0))
    assert g["blue_gain"] < 1.0 < g["red_gain"]


def test_gray_world_gains_epsilon_guards_zero_channel() -> None:
    from src.render.color_pipeline import gray_world_gains

    g = gray_world_gains((0.0, 128.0, 128.0))
    assert g["red_gain"] > 1.0  # finite, epsilon-guarded (no ZeroDivisionError)


def test_contrast_curve_points_keys_and_order() -> None:
    from src.render.color_pipeline import contrast_curve_points

    c = contrast_curve_points(0.05, 0.5, 0.95)
    assert set(c) == {"shadow_lift", "midtone", "highlight_roll"}
    assert all(0.0 <= v <= 1.0 for v in c.values())
    assert c["shadow_lift"] <= c["midtone"] <= c["highlight_roll"]


def test_kelvin_to_gains_neutral_at_reference() -> None:
    from src.render.color_pipeline import kelvin_to_gains

    neutral = kelvin_to_gains(5600)
    assert neutral["red_gain"] == pytest.approx(1.0, abs=0.05)
    assert neutral["green_gain"] == pytest.approx(1.0, abs=0.05)
    assert neutral["blue_gain"] == pytest.approx(1.0, abs=0.05)
    warm = kelvin_to_gains(3200)  # warm source -> neutralize: cut red, boost blue
    assert warm["red_gain"] < 1.0 < warm["blue_gain"]
    cool = kelvin_to_gains(8000)  # cool source -> neutralize: boost red, cut blue
    assert cool["blue_gain"] < 1.0 < cool["red_gain"]


# =========================================================================== #
# Group B — analyzers on real footage
# =========================================================================== #


def test_analyze_exposure_returns_float_in_range(sample_video: Path) -> None:
    from src.render.color_pipeline import analyze_exposure

    v = analyze_exposure(sample_video, 5.0, 45.0)
    assert isinstance(v, float)
    assert -1.0 <= v <= 1.0


def test_analyze_white_balance_returns_three_gain_values(sample_video: Path) -> None:
    from src.render.color_pipeline import analyze_white_balance

    wb = analyze_white_balance(sample_video, 5.0, 45.0)
    assert set(wb) == {"red_gain", "green_gain", "blue_gain"}
    assert all(isinstance(v, float) and v > 0 for v in wb.values())


def test_analyze_contrast_returns_curve_points(sample_video: Path) -> None:
    from src.render.color_pipeline import analyze_contrast

    c = analyze_contrast(sample_video, 5.0, 45.0)
    assert set(c) == {"shadow_lift", "midtone", "highlight_roll"}
    assert all(0.0 <= v <= 1.0 for v in c.values())
    assert c["shadow_lift"] <= c["midtone"] <= c["highlight_roll"]


def test_analyze_white_balance_detects_warm_footage(warm_clip: Path) -> None:
    from src.render.color_pipeline import analyze_white_balance

    wb = analyze_white_balance(warm_clip, 0.0, 2.0)
    assert wb["red_gain"] < 1.0 < wb["blue_gain"]


def test_analyze_white_balance_detects_cool_footage(cool_clip: Path) -> None:
    from src.render.color_pipeline import analyze_white_balance

    wb = analyze_white_balance(cool_clip, 0.0, 2.0)
    assert wb["blue_gain"] < 1.0 < wb["red_gain"]


# =========================================================================== #
# Group C — window-only sampling (monkeypatch the I/O seam)
# =========================================================================== #


def _patch_sampling(monkeypatch, captured: dict) -> None:
    import numpy as np

    import src.render.color_pipeline as cp

    def fake_grab(video_path, times):
        captured["times"] = list(times)
        return [np.full((4, 4, 3), 128, dtype=np.uint8) for _ in times]

    monkeypatch.setattr(cp, "_probe_duration", lambda _p: 60.0)
    monkeypatch.setattr(cp, "_grab_frames", fake_grab)


def test_analyze_exposure_samples_only_window(monkeypatch) -> None:
    import src.render.color_pipeline as cp

    captured: dict = {}
    _patch_sampling(monkeypatch, captured)
    cp.analyze_exposure(Path("dummy.mp4"), 10.0, 40.0)
    assert captured["times"]
    assert all(10.0 <= t <= 40.0 for t in captured["times"])


def test_analyze_white_balance_samples_only_window(monkeypatch) -> None:
    import src.render.color_pipeline as cp

    captured: dict = {}
    _patch_sampling(monkeypatch, captured)
    cp.analyze_white_balance(Path("dummy.mp4"), 15.0, 35.0)
    assert captured["times"]
    assert all(15.0 <= t <= 35.0 for t in captured["times"])


def test_analyze_contrast_samples_only_window(monkeypatch) -> None:
    import src.render.color_pipeline as cp

    captured: dict = {}
    _patch_sampling(monkeypatch, captured)
    cp.analyze_contrast(Path("dummy.mp4"), 20.0, 50.0)
    assert captured["times"]
    assert all(20.0 <= t <= 50.0 for t in captured["times"])


# =========================================================================== #
# Group D — build_correction_filter
# =========================================================================== #


def test_build_correction_filter_empty_when_all_none() -> None:
    from src.render.color_pipeline import build_correction_filter

    assert build_correction_filter(None, None, None) == ""


def test_build_correction_filter_composes_eq_and_colorbalance() -> None:
    from src.render.color_pipeline import build_correction_filter

    s = build_correction_filter(0.1, {"red_gain": 0.8, "green_gain": 1.0, "blue_gain": 1.25}, None)
    assert "eq=" in s and "brightness=" in s
    assert "colorbalance" in s
    assert "rm=" in s  # midtone band (not rs= shadows)
    assert "rm=-" in s  # red gain < 1 -> negative midtone red shift
    assert s.index("eq") < s.index("colorbalance")
    assert "curves" not in s


def test_build_correction_filter_contrast_curve() -> None:
    from src.render.color_pipeline import build_correction_filter

    s = build_correction_filter(
        None, None, {"shadow_lift": 0.05, "midtone": 0.5, "highlight_roll": 0.95}
    )
    assert "curves" in s and "all=" in s
    assert "eq" not in s and "colorbalance" not in s


def test_build_correction_filter_full_order_eq_colorbalance_curves() -> None:
    from src.render.color_pipeline import build_correction_filter

    s = build_correction_filter(
        0.1,
        {"red_gain": 0.8, "green_gain": 1.0, "blue_gain": 1.25},
        {"shadow_lift": 0.05, "midtone": 0.5, "highlight_roll": 0.95},
    )
    assert s.index("eq") < s.index("colorbalance") < s.index("curves")
    assert not s.startswith(",") and not s.endswith(",")


# =========================================================================== #
# Group E — get_correction_for_mode (policy layer)
# =========================================================================== #


def test_get_correction_disabled_returns_empty(non_video_file: Path, monkeypatch) -> None:
    import src.render.color_pipeline as cp
    from src.render.color_pipeline import get_correction_for_mode

    def _boom(*a, **k):
        raise AssertionError("analyzer must not run when disabled")

    monkeypatch.setattr(cp, "analyze_exposure", _boom)
    monkeypatch.setattr(cp, "analyze_white_balance", _boom)
    monkeypatch.setattr(cp, "analyze_contrast", _boom)

    out = get_correction_for_mode(non_video_file, 0.0, 10.0, _cp_config(enabled=False))
    assert out == ""


def test_get_correction_auto_mode_calls_analyzers(monkeypatch) -> None:
    import src.render.color_pipeline as cp
    from src.render.color_pipeline import get_correction_for_mode

    calls: dict = {}
    monkeypatch.setattr(cp, "analyze_exposure", lambda *a, **k: calls.setdefault("e", 0) or 0.1)
    monkeypatch.setattr(
        cp,
        "analyze_white_balance",
        lambda *a, **k: {"red_gain": 0.8, "green_gain": 1.0, "blue_gain": 1.2},
    )
    monkeypatch.setattr(
        cp,
        "analyze_contrast",
        lambda *a, **k: {"shadow_lift": 0.05, "midtone": 0.5, "highlight_roll": 0.95},
    )
    # record invocation via side flags
    seen = {"e": False, "w": False, "c": False}
    monkeypatch.setattr(cp, "analyze_exposure", lambda *a, **k: seen.update(e=True) or 0.1)
    monkeypatch.setattr(
        cp,
        "analyze_white_balance",
        lambda *a, **k: (
            seen.update(w=True) or {"red_gain": 0.8, "green_gain": 1.0, "blue_gain": 1.2}
        ),
    )
    monkeypatch.setattr(
        cp,
        "analyze_contrast",
        lambda *a, **k: (
            seen.update(c=True) or {"shadow_lift": 0.05, "midtone": 0.5, "highlight_roll": 0.95}
        ),
    )

    out = get_correction_for_mode(Path("x.mp4"), 1.0, 2.0, _cp_config())
    assert seen == {"e": True, "w": True, "c": True}
    assert "eq=" in out and "colorbalance" in out and "curves" in out


def test_get_correction_passes_window_boundaries(monkeypatch) -> None:
    import src.render.color_pipeline as cp
    from src.render.color_pipeline import get_correction_for_mode

    windows: dict = {}

    def rec(name):
        def _f(video_path, window_start_sec, window_end_sec, sample_frames=30):
            windows[name] = (window_start_sec, window_end_sec)
            if name == "e":
                return 0.1
            if name == "w":
                return {"red_gain": 0.9, "green_gain": 1.0, "blue_gain": 1.1}
            return {"shadow_lift": 0.05, "midtone": 0.5, "highlight_roll": 0.95}

        return _f

    monkeypatch.setattr(cp, "analyze_exposure", rec("e"))
    monkeypatch.setattr(cp, "analyze_white_balance", rec("w"))
    monkeypatch.setattr(cp, "analyze_contrast", rec("c"))

    get_correction_for_mode(Path("x.mp4"), 12.5, 47.5, _cp_config())
    assert windows["e"] == (12.5, 47.5)
    assert windows["w"] == (12.5, 47.5)
    assert windows["c"] == (12.5, 47.5)


def test_get_correction_override_skips_analysis(non_video_file: Path, monkeypatch) -> None:
    import src.render.color_pipeline as cp
    from src.render.color_pipeline import get_correction_for_mode

    def _boom(*a, **k):
        raise AssertionError("override mode must not sample the source")

    monkeypatch.setattr(cp, "analyze_exposure", _boom)
    monkeypatch.setattr(cp, "analyze_white_balance", _boom)
    monkeypatch.setattr(cp, "analyze_contrast", _boom)
    monkeypatch.setattr(cp, "_probe_duration", _boom)
    monkeypatch.setattr(cp, "_grab_frames", _boom)

    out = get_correction_for_mode(
        non_video_file,
        0.0,
        10.0,
        _cp_config(
            exposure_mode="override",
            exposure_override=0.2,
            wb_mode="override",
            wb_kelvin=4000,
            contrast_mode="override",
            contrast_curve="high",
        ),
    )
    assert "eq=" in out and "colorbalance" in out and "curves" in out


def test_get_correction_partial_override(monkeypatch) -> None:
    import src.render.color_pipeline as cp
    from src.render.color_pipeline import get_correction_for_mode

    def _boom_exposure(*a, **k):
        raise AssertionError("exposure override must not call analyze_exposure")

    monkeypatch.setattr(cp, "analyze_exposure", _boom_exposure)
    monkeypatch.setattr(
        cp,
        "analyze_white_balance",
        lambda *a, **k: {"red_gain": 0.8, "green_gain": 1.0, "blue_gain": 1.2},
    )
    monkeypatch.setattr(
        cp,
        "analyze_contrast",
        lambda *a, **k: {"shadow_lift": 0.05, "midtone": 0.5, "highlight_roll": 0.95},
    )

    out = get_correction_for_mode(
        Path("x.mp4"),
        3.0,
        9.0,
        _cp_config(exposure_mode="override", exposure_override=0.3),
    )
    assert "brightness=0.3" in out  # override value used directly
    assert "colorbalance" in out and "curves" in out  # auto WB + contrast applied


def test_get_correction_fails_loud_on_unreadable_source(non_video_file: Path) -> None:
    from src.render.color_pipeline import get_correction_for_mode

    with pytest.raises((ValueError, RuntimeError)):
        get_correction_for_mode(non_video_file, 0.0, 5.0, _cp_config())


# =========================================================================== #
# Group F — config schema + YAML loading
# =========================================================================== #


def test_config_technical_correction_defaults_disabled() -> None:
    from src.config import ColorPipelineConfig

    cfg = ColorPipelineConfig()
    assert cfg.technical_correction.enabled is False
    assert cfg.technical_correction.exposure.mode == "auto"
    assert cfg.technical_correction.white_balance.mode == "auto"
    assert cfg.technical_correction.contrast.mode == "auto"


def test_config_override_value_validation() -> None:
    from pydantic import ValidationError

    from src.config import ContrastSettings, ExposureSettings, WhiteBalanceSettings

    with pytest.raises(ValidationError):
        ExposureSettings(mode="override", override_value=2.0)
    with pytest.raises(ValidationError):
        ExposureSettings(mode="override", override_value=-1.5)
    with pytest.raises(ValidationError):
        WhiteBalanceSettings(mode="override", override_kelvin=1500)
    with pytest.raises(ValidationError):
        WhiteBalanceSettings(mode="override", override_kelvin=12000)
    with pytest.raises(ValidationError):
        ContrastSettings(mode="override", override_curve="ultra")
    # valid values accepted
    assert ExposureSettings(mode="override", override_value=0.5).override_value == 0.5
    assert WhiteBalanceSettings(mode="override", override_kelvin=4000).override_kelvin == 4000
    assert ContrastSettings(mode="override", override_curve="medium").override_curve == "medium"


def test_config_rejects_unknown_color_pipeline_key() -> None:
    from pydantic import ValidationError

    from src.config import ColorPipelineConfig

    with pytest.raises(ValidationError):
        ColorPipelineConfig(bogus=1)  # extra="forbid"


def test_cinematic_yaml_loads_color_pipeline_enabled() -> None:
    from src.config import load_mode_config

    mode = load_mode_config("cinematic_longform")
    assert mode.color_pipeline.technical_correction.enabled is True


def test_standard_longform_yaml_loads_color_pipeline_disabled() -> None:
    from src.config import load_mode_config

    mode = load_mode_config("standard_longform")
    assert mode.color_pipeline.technical_correction.enabled is False


def test_modeconfig_without_color_pipeline_defaults_disabled() -> None:
    from src.config import load_mode_config

    mode = load_mode_config("shortform")  # declares no color_pipeline block
    assert mode.color_pipeline.technical_correction.enabled is False


# =========================================================================== #
# Group G — build_longform_command correction integration (pure builder)
# =========================================================================== #


def _spec(correction=None, lut=None):
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec

    kwargs = {
        "source": Path("in.mp4"),
        "start_sec": 0.0,
        "end_sec": 300.0,
        "fmt": RenderFormat(width=1920, height=1080, fps=30),
        "output_path": Path("out.mp4"),
    }
    if correction is not None:
        kwargs["correction"] = correction
    if lut is not None:
        kwargs["lut"] = lut
    return LongformClipSpec(**kwargs)


def test_build_longform_command_includes_correction_when_enabled() -> None:
    from src.render.longform import LutGrade, build_longform_command

    spec = _spec(
        correction="eq=brightness=0.1,colorbalance=rm=-0.2:gm=0:bm=0.2",
        lut=LutGrade(path=Path("look.cube"), interp="tetrahedral"),
    )
    args = build_longform_command(spec, encoder="libx264")
    vf = args[args.index("-vf") + 1]
    assert vf == (
        "eq=brightness=0.1,colorbalance=rm=-0.2:gm=0:bm=0.2,"
        "lut3d=look.cube:interp=tetrahedral,scale=1920:1080"
    )


def test_build_longform_command_no_correction_when_disabled() -> None:
    from src.render.longform import LutGrade, build_longform_command

    spec = _spec(correction="", lut=LutGrade(path=Path("look.cube"), interp="tetrahedral"))
    assert spec.correction == ""  # field exists and defaults empty
    vf = build_longform_command(spec, encoder="libx264")[
        build_longform_command(spec, encoder="libx264").index("-vf") + 1
    ]
    assert vf == "lut3d=look.cube:interp=tetrahedral,scale=1920:1080"  # Phase 8 identical

    spec_nolut = _spec(correction="")
    vf2 = build_longform_command(spec_nolut, encoder="libx264")[
        build_longform_command(spec_nolut, encoder="libx264").index("-vf") + 1
    ]
    assert vf2 == "scale=1920:1080"  # Phase 7 identical


def test_filter_order_correction_before_lut() -> None:
    from src.render.longform import LutGrade, build_longform_command

    spec = _spec(correction="eq=brightness=0.1", lut=LutGrade(path=Path("look.cube")))
    vf = build_longform_command(spec, encoder="libx264")[
        build_longform_command(spec, encoder="libx264").index("-vf") + 1
    ]
    assert vf.index("eq=") < vf.index("lut3d") < vf.index("scale")


def test_build_longform_command_correction_without_lut() -> None:
    from src.render.longform import build_longform_command

    spec = _spec(correction="eq=brightness=0.1")
    vf = build_longform_command(spec, encoder="libx264")[
        build_longform_command(spec, encoder="libx264").index("-vf") + 1
    ]
    assert vf == "eq=brightness=0.1,scale=1920:1080"


# =========================================================================== #
# Group H — run_longform_render integration + Phase-6 cache
# =========================================================================== #


def test_run_longform_render_passes_window_to_color_pipeline(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.ingestion.workspace import create_workspace

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_win")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])

    seen: dict = {}

    def fake_get(video_path, window_start_sec, window_end_sec, color_pipeline_config):
        seen["window"] = (window_start_sec, window_end_sec)
        return "eq=brightness=0.0"

    monkeypatch.setattr(lf, "get_correction_for_mode", fake_get)

    backend = _RecordingLongformBackend()
    lf.run_longform_render(
        source, ws, load_config(), _cinematic_mode(), _moments(source, 5.0, 45.0), backend=backend
    )
    assert seen["window"] == (5.0, 45.0)  # best.start_sec, clamped end
    assert backend.specs[0].correction == "eq=brightness=0.0"


def test_run_longform_render_standard_sets_no_correction(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.ingestion.workspace import create_workspace

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_std")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])

    backend = _RecordingLongformBackend()
    lf.run_longform_render(
        source,
        ws,
        load_config(),
        _standard_mode(),
        _moments(source, 5.0, 45.0, mode="standard_longform"),
        backend=backend,
    )
    assert backend.specs[0].correction == ""


def test_color_correction_cache_miss_then_hit(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.ingestion.workspace import create_workspace

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_cache")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])

    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return "eq=brightness=0.0"

    monkeypatch.setattr(lf, "get_correction_for_mode", fake_get)

    cfg = load_config()
    for _ in range(2):
        backend = _RecordingLongformBackend()
        lf.run_longform_render(
            source, ws, cfg, _cinematic_mode(), _moments(source, 5.0, 45.0), backend=backend
        )
    assert calls["n"] == 1  # second render served from cache
    assert (ws.analysis_dir / "color_correction.json").exists()


def test_color_correction_cache_invalidates_on_window_change(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.ingestion.workspace import create_workspace

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_cache2")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])

    calls = {"n": 0}
    monkeypatch.setattr(
        lf,
        "get_correction_for_mode",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or "eq=brightness=0.0",
    )

    cfg = load_config()
    lf.run_longform_render(
        source,
        ws,
        cfg,
        _cinematic_mode(),
        _moments(source, 5.0, 45.0),
        backend=_RecordingLongformBackend(),
    )
    lf.run_longform_render(
        source,
        ws,
        cfg,
        _cinematic_mode(),
        _moments(source, 10.0, 50.0),
        backend=_RecordingLongformBackend(),
    )
    assert calls["n"] == 2  # different window -> different fingerprint -> recompute


def test_color_correction_cache_not_written_when_disabled(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.ingestion.workspace import create_workspace

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_nocache")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])

    backend = _RecordingLongformBackend()
    lf.run_longform_render(
        source,
        ws,
        load_config(),
        _standard_mode(),
        _moments(source, 5.0, 45.0, mode="standard_longform"),
        backend=backend,
    )
    assert backend.specs[0].correction == ""
    assert not (ws.analysis_dir / "color_correction.json").exists()
