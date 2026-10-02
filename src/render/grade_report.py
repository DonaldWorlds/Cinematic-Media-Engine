"""CP1 — auditable grade report (per-run QC sidecar for ``cme grade``).

Writes ``<output>.report.json`` next to a graded file and logs an INFO summary, capturing
what was applied: the input probe + measured stats, the pre-LUT BALANCE correction (with the
explicit ``contrast_normalization: false`` flag — the LUT owns contrast), the LUT and its
order, the encoder + the quality params actually used, and the output probe.

Best-effort: a report failure never breaks a successful grade (the probes degrade to empty).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from ..utils.ffmpeg import FFmpegError, ffprobe_json
from ..utils.fileio import write_json
from ..utils.logging import get_logger

if TYPE_CHECKING:
    from .color_pipeline import CorrectionResult

__all__ = [
    "CorrectionInfo",
    "EncoderInfo",
    "GradeReport",
    "InputInfo",
    "LutInfo",
    "OutputInfo",
    "build_grade_report",
    "emit_grade_report",
    "report_path_for",
    "write_grade_report",
]

_log = get_logger(__name__)

# Quality flags whose value (the next arg) records what the encoder ACTUALLY used
# (h264: -b:v/-crf/-preset/-q:v; prores: -profile:v).
_QUALITY_FLAGS = ("-b:v", "-crf", "-preset", "-q:v", "-profile:v")


class InputInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    codec: str | None = None
    pix_fmt: str | None = None
    width: int | None = None
    height: int | None = None
    color_range: str | None = None
    duration_sec: float | None = None
    mean_luma: float | None = None
    channel_means_rgb: tuple[float, float, float] | None = None


class CorrectionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    target_midgray: float | None = None
    exposure_offset: float | None = None
    wb_shifts: dict[str, float] | None = None
    filter: str = ""
    contrast_normalization: bool = False


class LutInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    interp: str
    applied_after_correction: bool = True
    source: str = "mode"  # which cube ran: "mode" (config) | "override" (--lut)


class EncoderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    quality: dict[str, str]


class OutputInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    pix_fmt: str | None = None
    width: int | None = None
    height: int | None = None
    bitrate: str | None = None


class GradeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: InputInfo
    correction: CorrectionInfo
    lut: LutInfo
    encoder: EncoderInfo
    output: OutputInfo


def _video_stream(probe: dict[str, Any]) -> dict[str, Any]:
    streams: list[dict[str, Any]] = probe.get("streams", [])
    for stream in streams:
        if stream.get("codec_type") == "video":
            return stream
    return {}


def _to_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _encoder_quality_from_args(args: list[str]) -> dict[str, str]:
    """The quality params actually used, read off the final ffmpeg args (order: bitrate/crf/q:v)."""
    quality: dict[str, str] = {}
    for flag in _QUALITY_FLAGS:
        if flag in args:
            quality[flag.lstrip("-")] = args[args.index(flag) + 1]
    return quality


def report_path_for(output: Path) -> Path:
    """``<output>.report.json`` (suffix appended, not replaced — so it sits beside the file)."""
    return output.with_name(output.name + ".report.json")


def build_grade_report(
    *,
    source: Path,
    output: Path,
    source_probe: dict[str, Any],
    output_probe: dict[str, Any] | None,
    correction: CorrectionResult | None,
    lut_path: Path,
    lut_interp: str,
    encoder: str,
    quality_params: dict[str, str],
    lut_source: str = "mode",
) -> GradeReport:
    """Assemble a :class:`GradeReport` from probes + the resolved correction (pure, no I/O)."""
    sv = _video_stream(source_probe)
    sfmt = source_probe.get("format", {})
    measured = correction if (correction is not None and correction.enabled) else None
    input_info = InputInfo(
        path=str(source),
        codec=sv.get("codec_name"),
        pix_fmt=sv.get("pix_fmt"),
        width=_to_int(sv.get("width")),
        height=_to_int(sv.get("height")),
        color_range=sv.get("color_range"),
        duration_sec=_to_float(sfmt.get("duration")),
        mean_luma=measured.mean_luma if measured else None,
        channel_means_rgb=measured.channel_means_rgb if measured else None,
    )
    correction_info = CorrectionInfo(
        enabled=bool(correction and correction.enabled),
        target_midgray=correction.target_midgray if correction else None,
        exposure_offset=correction.exposure_offset if correction else None,
        wb_shifts=correction.wb_shifts if correction else None,
        filter=correction.filter_string if correction else "",
        contrast_normalization=bool(correction.contrast_normalization) if correction else False,
    )
    ov = _video_stream(output_probe or {})
    ofmt = (output_probe or {}).get("format", {})
    output_info = OutputInfo(
        path=str(output),
        pix_fmt=ov.get("pix_fmt"),
        width=_to_int(ov.get("width")),
        height=_to_int(ov.get("height")),
        bitrate=ofmt.get("bit_rate"),
    )
    return GradeReport(
        input=input_info,
        correction=correction_info,
        lut=LutInfo(
            path=str(lut_path),
            interp=lut_interp,
            applied_after_correction=True,
            source=lut_source,
        ),
        encoder=EncoderInfo(name=encoder, quality=dict(quality_params)),
        output=output_info,
    )


def write_grade_report(report: GradeReport, output: Path) -> Path:
    """Write ``report`` to ``<output>.report.json`` and return that path."""
    path = report_path_for(output)
    write_json(path, report.model_dump())
    return path


def _safe_probe(path: Path) -> dict[str, Any]:
    try:
        return ffprobe_json(path)
    except FFmpegError:
        return {}


def emit_grade_report(
    *,
    source: Path,
    output: Path,
    correction: CorrectionResult | None,
    lut_path: Path,
    lut_interp: str,
    encoder: str,
    cmd: list[str],
    lut_source: str = "mode",
) -> Path | None:
    """Probe input/output, assemble, write the sidecar, and log an INFO summary.

    Best-effort: a missing/unprobeable file degrades to empty probe data rather than failing
    the (already-successful) grade.
    """
    report = build_grade_report(
        source=source,
        output=output,
        source_probe=_safe_probe(source),
        output_probe=_safe_probe(output),
        correction=correction,
        lut_path=lut_path,
        lut_interp=lut_interp,
        encoder=encoder,
        quality_params=_encoder_quality_from_args(cmd),
        lut_source=lut_source,
    )
    path = write_grade_report(report, output)
    _log.info(
        "grade report -> %s | correction=%s | contrast_normalization=%s | encoder=%s",
        path,
        report.correction.filter or "(none)",
        report.correction.contrast_normalization,
        encoder,
    )
    return path
