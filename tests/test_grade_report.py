"""CP1 — auditable grade report (written before implementation).

A per-run QC sidecar (``<output>.report.json``) capturing what `cme grade` applied:
input probe + measured stats, the pre-LUT correction (with the explicit
``contrast_normalization: false`` flag), the LUT and its order, the encoder + the
quality params actually used, and the output probe. Pure assembly is unit-tested with
hand-built ffprobe dicts; the run_grade wiring is tested with ffprobe monkeypatched.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _source_probe() -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv422p10le",
                "width": 3840,
                "height": 2160,
                "color_range": "pc",
            },
            {"codec_type": "audio", "codec_name": "pcm_s16be"},
        ],
        "format": {"duration": "67.07"},
    }


def _output_probe() -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 3840,
                "height": 2160,
            }
        ],
        "format": {"bit_rate": "98000000"},
    }


def _correction():
    from src.render.color_pipeline import CorrectionResult

    return CorrectionResult(
        enabled=True,
        filter_string="eq=brightness=-0.05,colorbalance=rm=0.02:gm=0:bm=-0.02",
        contrast_normalization=False,
        mean_luma=0.46,
        channel_means_rgb=(120.0, 118.0, 122.0),
        target_midgray=0.41,
        exposure_offset=-0.05,
        wb_shifts={"rm": 0.02, "gm": 0.0, "bm": -0.02},
    )


# --------------------------------------------------------------------------- #
# encoder-quality parse (the params ACTUALLY used, read off the final args)
# --------------------------------------------------------------------------- #


def test_encoder_quality_from_args_hardware_qv() -> None:
    from src.render.grade_report import _encoder_quality_from_args

    q = _encoder_quality_from_args(["-c:v", "h264_videotoolbox", "-q:v", "65", "-c:a", "copy"])
    assert q == {"q:v": "65"}


def test_encoder_quality_from_args_software_crf_preset() -> None:
    from src.render.grade_report import _encoder_quality_from_args

    q = _encoder_quality_from_args(["-c:v", "libx264", "-crf", "18", "-preset", "medium"])
    assert q == {"crf": "18", "preset": "medium"}


def test_encoder_quality_from_args_bitrate() -> None:
    from src.render.grade_report import _encoder_quality_from_args

    assert _encoder_quality_from_args(["-c:v", "libx264", "-b:v", "50M"]) == {"b:v": "50M"}


def test_encoder_quality_from_args_prores_profile() -> None:
    """CP2: the prores master records its profile so the report shows the encoder settings."""
    from src.render.grade_report import _encoder_quality_from_args

    q = _encoder_quality_from_args(
        ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    )
    assert q == {"profile:v": "3"}


def test_report_path_appends_report_json() -> None:
    from src.render.grade_report import report_path_for

    assert report_path_for(Path("/o/test15_graded.mov")) == Path("/o/test15_graded.mov.report.json")


# --------------------------------------------------------------------------- #
# build_grade_report — pure assembly
# --------------------------------------------------------------------------- #


def test_build_grade_report_assembles_all_sections() -> None:
    from src.render.grade_report import build_grade_report

    r = build_grade_report(
        source=Path("in.MP4"),
        output=Path("out.mov"),
        source_probe=_source_probe(),
        output_probe=_output_probe(),
        correction=_correction(),
        lut_path=Path("look.cube"),
        lut_interp="tetrahedral",
        encoder="h264_videotoolbox",
        quality_params={"q:v": "65"},
    )
    # input + measured stats
    assert r.input.codec == "h264"
    assert r.input.pix_fmt == "yuv422p10le"
    assert (r.input.width, r.input.height) == (3840, 2160)
    assert r.input.color_range == "pc"
    assert r.input.duration_sec == pytest.approx(67.07)
    assert r.input.mean_luma == pytest.approx(0.46)
    assert r.input.channel_means_rgb == (120.0, 118.0, 122.0)
    # correction (with the explicit audit flag)
    assert r.correction.enabled is True
    assert r.correction.target_midgray == pytest.approx(0.41)
    assert r.correction.exposure_offset == pytest.approx(-0.05)
    assert r.correction.wb_shifts == {"rm": 0.02, "gm": 0.0, "bm": -0.02}
    assert "curves" not in r.correction.filter
    assert r.correction.contrast_normalization is False
    # lut + order
    assert r.lut.path.endswith("look.cube")
    assert r.lut.interp == "tetrahedral"
    assert r.lut.applied_after_correction is True
    # encoder params actually used
    assert r.encoder.name == "h264_videotoolbox"
    assert r.encoder.quality == {"q:v": "65"}
    # output probe
    assert r.output.pix_fmt == "yuv420p"
    assert (r.output.width, r.output.height) == (3840, 2160)
    assert r.output.bitrate == "98000000"


def test_build_grade_report_no_correction_marks_disabled() -> None:
    from src.render.grade_report import build_grade_report

    r = build_grade_report(
        source=Path("in.MP4"),
        output=Path("out.mov"),
        source_probe=_source_probe(),
        output_probe=_output_probe(),
        correction=None,  # --no-correct
        lut_path=Path("look.cube"),
        lut_interp="tetrahedral",
        encoder="libx264",
        quality_params={"crf": "18", "preset": "medium"},
    )
    assert r.correction.enabled is False
    assert r.correction.filter == ""
    assert r.correction.contrast_normalization is False
    assert r.input.mean_luma is None  # not measured when correction is off


def test_build_grade_report_records_lut_source() -> None:
    """CP: the report records which cube ran — 'mode' vs '--lut override'."""
    from src.render.grade_report import build_grade_report

    r = build_grade_report(
        source=Path("in.MP4"),
        output=Path("out.mov"),
        source_probe=_source_probe(),
        output_probe=_output_probe(),
        correction=_correction(),
        lut_path=Path("/abs/override.cube"),
        lut_interp="tetrahedral",
        lut_source="override",
        encoder="prores_ks",
        quality_params={"profile:v": "3"},
    )
    assert r.lut.source == "override"
    assert r.lut.path.endswith("override.cube")


def test_write_grade_report_writes_sidecar(tmp_path: Path) -> None:
    from src.render.grade_report import build_grade_report, write_grade_report
    from src.utils.fileio import read_json

    out = tmp_path / "g.mov"
    r = build_grade_report(
        source=Path("in.MP4"),
        output=out,
        source_probe=_source_probe(),
        output_probe=_output_probe(),
        correction=_correction(),
        lut_path=Path("look.cube"),
        lut_interp="tetrahedral",
        encoder="h264_videotoolbox",
        quality_params={"q:v": "65"},
    )
    p = write_grade_report(r, out)
    assert p == tmp_path / "g.mov.report.json"
    data = read_json(p)
    assert set(data) >= {"input", "correction", "lut", "encoder", "output"}
    assert data["correction"]["contrast_normalization"] is False
    assert "curves" not in data["correction"]["filter"]


# --------------------------------------------------------------------------- #
# run_grade wiring — emits the sidecar after a successful render
# --------------------------------------------------------------------------- #


def test_run_grade_emits_report_sidecar(tmp_path: Path, monkeypatch) -> None:
    import src.render.grade_report as gr
    import src.render.longform as lf
    from src.config import load_config, load_mode_config
    from src.render.color_pipeline import CorrectionResult
    from src.render.longform import LutGrade, run_grade
    from src.utils.fileio import read_json

    monkeypatch.setattr(
        lf,
        "resolve_lut_grade",
        lambda cg: LutGrade(path=Path("tests/fixtures/identity.cube"), interp="tetrahedral"),
    )
    monkeypatch.setattr(lf, "run_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(
        lf,
        "resolve_correction",
        lambda *a, **k: CorrectionResult(
            enabled=True,
            filter_string="eq=brightness=-0.05,colorbalance=rm=0:gm=0:bm=0",
            contrast_normalization=False,
            mean_luma=0.46,
            channel_means_rgb=(120.0, 118.0, 122.0),
            target_midgray=0.41,
            exposure_offset=-0.05,
            wb_shifts={"rm": 0.0, "gm": 0.0, "bm": 0.0},
        ),
    )
    monkeypatch.setattr(
        gr,
        "ffprobe_json",
        lambda p: {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv422p10le",
                    "width": 3840,
                    "height": 2160,
                    "color_range": "pc",
                }
            ],
            "format": {"duration": "67.07", "bit_rate": "98000000"},
        },
    )

    out = tmp_path / "graded.mov"
    run_grade(Path("in.MP4"), out, load_config(), load_mode_config("cinematic_longform"))

    report = out.with_name(out.name + ".report.json")
    assert report.exists()
    data = read_json(report)
    assert data["correction"]["contrast_normalization"] is False
    assert "curves" not in data["correction"]["filter"]
    assert data["lut"]["applied_after_correction"] is True
    assert data["encoder"]["name"] == "prores_ks"  # default master is now ProRes 422 HQ
    assert data["encoder"]["quality"] == {"profile:v": "3"}  # profile recorded in the report
