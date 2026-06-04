"""Pydantic config schemas + loaders for config.yaml and content_modes/*.yaml."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DEFAULT_MODES_DIR = PROJECT_ROOT / "content_modes"


# --------------------------------------------------------------------------- #
# Root config (config.yaml) — modelled exactly; extra keys are rejected.
# --------------------------------------------------------------------------- #


class TwelveLabsAPIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key_env: str
    index_id_env: str
    timeout_seconds: int = 1800
    required: bool = False


class APIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    twelvelabs: TwelveLabsAPIConfig


class DefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    workspace_root: Path
    retention_days: int


class TranscriptionAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    device: str


class TranscriptionDiarization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class TranscriptionAudioPreprocess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loudnorm: bool
    sample_rate: int
    mono: bool


class TranscriptionConfig(BaseModel):
    # protected_namespaces=() allows the `model_size` field (the `model_`
    # prefix is otherwise reserved by pydantic).
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    backend: str
    model_size: str
    device: str
    compute_type: str
    language: str
    word_timestamps: bool
    beam_size: int
    condition_on_previous_text: bool
    vad_filter: bool
    vad_min_silence_ms: int
    alignment: TranscriptionAlignment
    diarization: TranscriptionDiarization
    audio_preprocess: TranscriptionAudioPreprocess


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_rate: int
    energy_window_sec: float
    peak_threshold_std: float
    min_peak_gap_sec: float


class OpenCVConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_detect_threshold: float
    motion_sample_rate_hz: float
    enabled: bool


class OpenCLIPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    model_name: str
    pretrained: str
    device: str
    sample_rate_hz: float
    batch_size: int
    enabled: bool


class AestheticsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    sample_rate_hz: float
    metrics: list[str]


class SubjectsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    enabled: bool
    model: str
    device: str
    sample_rate_hz: float
    confidence: float


class ReframeDecisionConfig(BaseModel):
    """Thresholds for the auto-reframe decision engine (Phase 4.5 Layer 2)."""

    model_config = ConfigDict(extra="forbid")
    solo_track_max_width: float  # solo box wider than this (frac of frame) -> blur_pad, not track
    bucket_2_fs: float  # exactly 2 people
    bucket_3_5_fs: float  # 3..small_group_max people (tuned to 1.0: preserve edges, accept bars)
    small_group_max: int
    bucket_6_plus_fs: float  # >small_group_max, 0, or solo-too-wide -> keep everyone


class ZoomCropConfig(BaseModel):
    """Zoom factor for the ``zoom_crop`` reframe (parallel dual-output framing)."""

    model_config = ConfigDict(extra="forbid")
    foreground_scale: float  # cover-scale punch-in: 1.0 == center_crop; >1.0 zooms further


class HooksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    hook_phrases: list[str]
    hook_window_sec: float


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    composite_method: str
    normalize_range: list[float]
    cluster_min_gap_sec: float
    default_threshold: float
    # Window aggregation (Phase 3.5): how a window's per-second signal values
    # collapse to one score. "percentile" (default) spreads scores by capturing the
    # near-peak; "mean" regresses to the average. Modes may override.
    window_aggregation: str = "percentile"
    window_percentile: float = 90.0


class OutputFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int
    height: int
    fps: int
    codec: str


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_dir: Path
    formats: dict[str, OutputFormat]


class FFmpegConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binary_path: str
    ffprobe_path: str
    hardware_acceleration: str
    preset: str
    threads: int


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subtitle_default_format: str
    transition_default: str
    branding_default_intensity: str


class ThumbnailResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int
    height: int


class ThumbnailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    count: int
    selection_method: str
    output_format: str
    resolution: ThumbnailResolution


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: Path
    retention_days: int
    cleanup_on_start: bool
    preserve_on_error: bool


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str
    log_to_file: bool
    log_file_path: Path
    rich_console: bool


class PerformanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parallel_analysis: bool
    max_workers: int  # LIGHT lane cap (librosa/opencv/aesthetics/hooks)
    twelvelabs_async: bool
    # Phase 6 thread-lane caps (CP1.5). Defaulted so pre-Phase-6 configs still load.
    max_heavy_workers: int = 1  # HEAVY lane (whisper/subjects): one GB-class model at a time on 8GB
    max_render_workers: int = 2  # RENDER lane (per-clip ffmpeg): videotoolbox session-safe


class ContentModesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_dir: Path
    available: list[str]


class BrandingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assets_dir: Path
    luts_dir: Path
    fonts_dir: Path
    overlays_dir: Path
    logos_dir: Path
    presets_dir: Path
    lower_thirds_dir: Path


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defaults: DefaultsConfig
    api: APIConfig
    transcription: TranscriptionConfig
    audio: AudioConfig
    opencv: OpenCVConfig
    openclip: OpenCLIPConfig
    aesthetics: AestheticsConfig
    subjects: SubjectsConfig
    reframe_decision: ReframeDecisionConfig
    zoom_crop_decision: ZoomCropConfig
    hooks: HooksConfig
    scoring: ScoringConfig
    output: OutputConfig
    ffmpeg: FFmpegConfig
    render: RenderConfig
    thumbnails: ThumbnailsConfig
    workspace: WorkspaceConfig
    logging: LoggingConfig
    performance: PerformanceConfig
    content_modes: ContentModesConfig
    branding: BrandingConfig


# --------------------------------------------------------------------------- #
# Mode config (content_modes/*.yaml) — common fields typed, extras allowed.
# --------------------------------------------------------------------------- #


class ModeTranscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    alignment_required: bool = False
    opt_in_flag: str | None = None
    gates_srt: bool = False


class DescriptSettings(BaseModel):
    """Per-mode Descript handoff config (Phase 9). Disabled unless a mode opts in."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    prompt: str = ""  # SINGLE combined Underlord prompt (one /jobs/agent call)
    publish_resolution: Literal["480p", "720p", "1080p", "1440p", "4K"] = "1080p"
    polling_interval_sec: int = 10
    timeout_sec: int = 900
    music_path: Path | None = None  # cinematic only: user-provided bed, imported then prompted


# --------------------------------------------------------------------------- #
# Phase 10 — colour pipeline (Stage-1 technical correction). The Stage-2 creative
# grade stays in the existing top-level ``color_grade`` block (unchanged).
# --------------------------------------------------------------------------- #

_CONTRAST_CURVES = ("soft", "medium", "high")


class ExposureSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "override"] = "auto"
    override_value: float | None = None  # only used when mode == "override"

    @field_validator("override_value")
    @classmethod
    def _in_range(cls, value: float | None) -> float | None:
        if value is not None and not (-1.0 <= value <= 1.0):
            raise ValueError(f"exposure override_value must be in [-1.0, 1.0], got {value}")
        return value


class WhiteBalanceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "override"] = "auto"
    override_kelvin: int | None = None  # only used when mode == "override"

    @field_validator("override_kelvin")
    @classmethod
    def _in_range(cls, value: int | None) -> int | None:
        if value is not None and not (2000 <= value <= 10000):
            raise ValueError(f"white_balance override_kelvin must be in [2000, 10000], got {value}")
        return value


class ContrastSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "override"] = "auto"
    override_curve: str | None = None  # "soft" | "medium" | "high" | None

    @field_validator("override_curve")
    @classmethod
    def _known_curve(cls, value: str | None) -> str | None:
        if value is not None and value not in _CONTRAST_CURVES:
            raise ValueError(
                f"contrast override_curve must be one of {list(_CONTRAST_CURVES)} or null, "
                f"got {value!r}"
            )
        return value


class TechnicalCorrectionSettings(BaseModel):
    """Stage-1 correction toggle + per-component auto/override settings (Phase 10)."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False  # default off; cinematic_longform.yaml turns it on
    exposure: ExposureSettings = Field(default_factory=ExposureSettings)
    white_balance: WhiteBalanceSettings = Field(default_factory=WhiteBalanceSettings)
    contrast: ContrastSettings = Field(default_factory=ContrastSettings)


class ColorPipelineConfig(BaseModel):
    """Per-mode colour pipeline config. Holds Stage-1 technical correction only; the
    Stage-2 LUT grade stays in the existing top-level ``color_grade`` block."""

    model_config = ConfigDict(extra="forbid")
    technical_correction: TechnicalCorrectionSettings = Field(
        default_factory=TechnicalCorrectionSettings
    )


class ModeConfig(BaseModel):
    # extra="allow": mode-specific keys (diversity, captions, music_sync,
    # twelvelabs_queries, score_threshold, target_*) vary by mode and are
    # consumed by later-phase modules; only the common fields are typed here.
    model_config = ConfigDict(extra="allow")
    mode_name: str
    transcription: ModeTranscription
    scoring_weights: dict[str, float]
    pacing: dict[str, Any]
    rendering: dict[str, Any]
    output: dict[str, Any]
    clip_density: str
    output_targets: list[str] = []
    descript: DescriptSettings = Field(default_factory=DescriptSettings)
    color_pipeline: ColorPipelineConfig = Field(default_factory=ColorPipelineConfig)

    @field_validator("scoring_weights")
    @classmethod
    def _weights_sum_to_one(cls, value: dict[str, float]) -> dict[str, float]:
        total = sum(value.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"scoring_weights must sum to 1.0, got {total}")
        return value


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping at top of {path}, got {type(data).__name__}")
    return data


def load_config(path: Path | None = None) -> Config:
    """Load and validate the root config.yaml."""
    cfg_path = path if path is not None else DEFAULT_CONFIG_PATH
    return Config.model_validate(_read_yaml(cfg_path))


def load_mode_config(mode_name: str, modes_dir: Path | None = None) -> ModeConfig:
    """Load and validate ``content_modes/<mode_name>.yaml``.

    Raises ValueError for an unknown mode.
    """
    base = modes_dir if modes_dir is not None else DEFAULT_MODES_DIR
    mode_path = base / f"{mode_name}.yaml"
    if not mode_path.exists():
        raise ValueError(f"unknown content mode {mode_name!r}: {mode_path} not found")
    return ModeConfig.model_validate(_read_yaml(mode_path))
