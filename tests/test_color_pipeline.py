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
):
    from src.config import (
        ColorPipelineConfig,
        ExposureSettings,
        TechnicalCorrectionSettings,
        WhiteBalanceSettings,
    )

    return ColorPipelineConfig(
        technical_correction=TechnicalCorrectionSettings(
            enabled=enabled,
            exposure=ExposureSettings(mode=exposure_mode, override_value=exposure_override),
            white_balance=WhiteBalanceSettings(mode=wb_mode, override_kelvin=wb_kelvin),
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


def test_exposure_adjustment_balances_toward_log_midgray_and_is_bounded() -> None:
    """Balance toward the log mid-gray target (default 0.41, NOT display 0.5), clamped to a
    gentle ``max_shift`` so a pre-LUT correction nudges rather than normalizes (CP1)."""
    from src.render.color_pipeline import exposure_adjustment

    # already at the log mid-gray target -> no shift
    assert exposure_adjustment(0.41, target=0.41, max_shift=0.10) == pytest.approx(0.0)
    # slightly under target -> small positive nudge (within bound)
    assert exposure_adjustment(0.35, target=0.41, max_shift=0.10) == pytest.approx(0.06)
    # far under -> clamped UP to +max_shift (balance, never slam to 0.41)
    assert exposure_adjustment(0.0, target=0.41, max_shift=0.10) == pytest.approx(0.10)
    # far over -> clamped DOWN to -max_shift
    assert exposure_adjustment(0.9, target=0.41, max_shift=0.10) == pytest.approx(-0.10)
    # default target is the S-Log3 log mid-gray (0.41), not display mid-gray (0.5)
    assert exposure_adjustment(0.41) == pytest.approx(0.0)
    assert exposure_adjustment(0.5) != pytest.approx(0.0)


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


def test_contrast_helpers_are_removed() -> None:
    """CP1: pre-LUT contrast normalization is gone — the LUT owns contrast. The symbols
    must not exist on the module (guards against a silent re-introduction)."""
    import src.render.color_pipeline as cp

    assert not hasattr(cp, "contrast_curve_points")
    assert not hasattr(cp, "analyze_contrast")
    assert "analyze_contrast" not in cp.__all__
    assert "contrast_curve_points" not in cp.__all__


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


# =========================================================================== #
# Group D — build_correction_filter
# =========================================================================== #


def test_build_correction_filter_empty_when_all_none() -> None:
    from src.render.color_pipeline import build_correction_filter

    assert build_correction_filter(None, None) == ""


def test_build_correction_filter_composes_eq_and_colorbalance() -> None:
    from src.render.color_pipeline import build_correction_filter

    s = build_correction_filter(0.1, {"red_gain": 0.8, "green_gain": 1.0, "blue_gain": 1.25})
    assert "eq=" in s and "brightness=" in s
    assert "colorbalance" in s
    assert "rm=" in s  # midtone band (not rs= shadows)
    assert "rm=-" in s  # red gain < 1 -> negative midtone red shift
    assert s.index("eq") < s.index("colorbalance")
    assert not s.startswith(",") and not s.endswith(",")


def test_build_correction_filter_never_emits_curves() -> None:
    """CP1 core invariant: a pre-LUT correction NEVER contains a contrast curve — that
    dynamic-range expansion is the white-out; the LUT owns contrast."""
    from src.render.color_pipeline import build_correction_filter

    s = build_correction_filter(0.1, {"red_gain": 0.8, "green_gain": 1.0, "blue_gain": 1.25})
    assert "curves" not in s
    # exposure-only and wb-only forms are equally curve-free
    assert "curves" not in build_correction_filter(0.1, None)
    assert "curves" not in build_correction_filter(
        None, {"red_gain": 0.9, "green_gain": 1.0, "blue_gain": 1.1}
    )


def test_build_correction_filter_wb_shift_is_a_bounded_nudge() -> None:
    """WB stays a gentle balance: extreme gray-world gains clamp to ``wb_max_shift``,
    not the full +-1 colorbalance range."""
    from src.render.color_pipeline import build_correction_filter

    s = build_correction_filter(
        None, {"red_gain": 5.0, "green_gain": 1.0, "blue_gain": 0.01}, wb_max_shift=0.10
    )
    assert "rm=0.1" in s  # +inf gain clamps UP to +max_shift
    assert "bm=-0.1" in s  # ~0 gain clamps DOWN to -max_shift
    assert "curves" not in s


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

    out = get_correction_for_mode(non_video_file, 0.0, 10.0, _cp_config(enabled=False))
    assert out == ""


def test_get_correction_auto_mode_produces_eq_and_colorbalance_no_curves(monkeypatch) -> None:
    captured: dict = {}
    _patch_sampling(monkeypatch, captured)

    from src.render.color_pipeline import get_correction_for_mode

    out = get_correction_for_mode(Path("x.mp4"), 1.0, 2.0, _cp_config())
    assert "eq=" in out and "colorbalance" in out  # exposure balance + WB nudge
    assert "curves" not in out  # CP1: the LUT owns contrast
    assert captured["times"]  # the window was sampled (once) for both components


def test_get_correction_passes_window_boundaries(monkeypatch) -> None:
    captured: dict = {}
    _patch_sampling(monkeypatch, captured)

    from src.render.color_pipeline import get_correction_for_mode

    get_correction_for_mode(Path("x.mp4"), 12.5, 47.5, _cp_config())
    assert captured["times"]
    assert all(12.5 <= t <= 47.5 for t in captured["times"])  # sampled strictly inside the window


def test_get_correction_override_skips_analysis(non_video_file: Path, monkeypatch) -> None:
    import src.render.color_pipeline as cp
    from src.render.color_pipeline import get_correction_for_mode

    def _boom(*a, **k):
        raise AssertionError("override mode must not sample the source")

    monkeypatch.setattr(cp, "analyze_exposure", _boom)
    monkeypatch.setattr(cp, "analyze_white_balance", _boom)
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
        ),
    )
    assert "eq=" in out and "colorbalance" in out
    assert "curves" not in out


def test_get_correction_partial_override(monkeypatch) -> None:
    captured: dict = {}
    _patch_sampling(monkeypatch, captured)

    from src.render.color_pipeline import get_correction_for_mode

    out = get_correction_for_mode(
        Path("x.mp4"),
        3.0,
        9.0,
        _cp_config(exposure_mode="override", exposure_override=0.3),
    )
    assert "brightness=0.3" in out  # exposure override used directly (not measured)
    assert "colorbalance" in out  # WB still auto-balanced from the sampled frames
    assert "curves" not in out  # no contrast stage


def test_get_correction_fails_loud_on_unreadable_source(non_video_file: Path) -> None:
    from src.render.color_pipeline import get_correction_for_mode

    with pytest.raises((ValueError, RuntimeError)):
        get_correction_for_mode(non_video_file, 0.0, 5.0, _cp_config())


# =========================================================================== #
# Group E2 — resolve_correction: structured result for the audit report
# =========================================================================== #


def test_get_correction_for_mode_is_the_filter_string_of_resolve_correction(monkeypatch) -> None:
    """``get_correction_for_mode`` is a thin wrapper over ``resolve_correction`` (the cut
    path keeps calling it and gets the same filter string)."""
    captured: dict = {}
    _patch_sampling(monkeypatch, captured)

    from src.render.color_pipeline import get_correction_for_mode, resolve_correction

    cfg = _cp_config()
    result = resolve_correction(Path("x.mp4"), 1.0, 2.0, cfg)
    filt = get_correction_for_mode(Path("x.mp4"), 1.0, 2.0, cfg)
    assert filt == result.filter_string


def test_resolve_correction_returns_structured_values(monkeypatch) -> None:
    captured: dict = {}
    _patch_sampling(monkeypatch, captured)  # solid mid-gray (128) frames

    from src.render.color_pipeline import resolve_correction

    r = resolve_correction(Path("x.mp4"), 1.0, 2.0, _cp_config())
    assert r.enabled is True
    assert isinstance(r.mean_luma, float) and 0.0 <= r.mean_luma <= 1.0
    assert r.channel_means_rgb is not None and len(r.channel_means_rgb) == 3
    assert r.target_midgray == pytest.approx(0.41)  # default log mid-gray
    assert isinstance(r.exposure_offset, float)
    assert set(r.wb_shifts) == {"rm", "gm", "bm"}
    assert "eq=" in r.filter_string and "colorbalance" in r.filter_string
    assert "curves" not in r.filter_string
    assert r.contrast_normalization is False  # the explicit audit flag: the LUT owns contrast


def test_resolve_correction_samples_the_window_exactly_once(monkeypatch) -> None:
    """One sampling pass feeds BOTH exposure and WB (no double-decode of 4K frames)."""
    import numpy as np

    import src.render.color_pipeline as cp

    calls = {"n": 0}

    def counting_grab(video_path, times):
        calls["n"] += 1
        return [np.full((4, 4, 3), 128, dtype=np.uint8) for _ in times]

    monkeypatch.setattr(cp, "_probe_duration", lambda _p: 60.0)
    monkeypatch.setattr(cp, "_grab_frames", counting_grab)

    cp.resolve_correction(Path("x.mp4"), 1.0, 2.0, _cp_config())
    assert calls["n"] == 1  # exposure + WB share a single sampling pass


def test_resolve_correction_disabled_returns_empty_unsampled(monkeypatch) -> None:
    import src.render.color_pipeline as cp

    def _boom(*a, **k):
        raise AssertionError("disabled correction must not sample")

    monkeypatch.setattr(cp, "_probe_duration", _boom)
    monkeypatch.setattr(cp, "_grab_frames", _boom)

    r = cp.resolve_correction(Path("x.mp4"), 0.0, 10.0, _cp_config(enabled=False))
    assert r.enabled is False
    assert r.filter_string == ""
    assert r.contrast_normalization is False


# =========================================================================== #
# Group F — config schema + YAML loading
# =========================================================================== #


def test_config_technical_correction_defaults_disabled() -> None:
    from src.config import ColorPipelineConfig

    cfg = ColorPipelineConfig()
    assert cfg.technical_correction.enabled is False
    assert cfg.technical_correction.exposure.mode == "auto"
    assert cfg.technical_correction.white_balance.mode == "auto"
    # CP1 balance knobs: log mid-gray target + gentle bounds (defaults), no contrast block
    assert cfg.technical_correction.exposure.target_midgray == pytest.approx(0.41)
    assert cfg.technical_correction.exposure.max_shift == pytest.approx(0.10)
    assert cfg.technical_correction.white_balance.max_shift == pytest.approx(0.10)
    assert not hasattr(cfg.technical_correction, "contrast")


def test_config_contrast_settings_removed() -> None:
    import src.config as config

    assert not hasattr(config, "ContrastSettings")  # CP1: contrast no longer a pre-LUT stage


def test_config_override_value_validation() -> None:
    from pydantic import ValidationError

    from src.config import ExposureSettings, WhiteBalanceSettings

    with pytest.raises(ValidationError):
        ExposureSettings(mode="override", override_value=2.0)
    with pytest.raises(ValidationError):
        ExposureSettings(mode="override", override_value=-1.5)
    with pytest.raises(ValidationError):
        WhiteBalanceSettings(mode="override", override_kelvin=1500)
    with pytest.raises(ValidationError):
        WhiteBalanceSettings(mode="override", override_kelvin=12000)
    # valid values accepted
    assert ExposureSettings(mode="override", override_value=0.5).override_value == 0.5
    assert WhiteBalanceSettings(mode="override", override_kelvin=4000).override_kelvin == 4000


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
