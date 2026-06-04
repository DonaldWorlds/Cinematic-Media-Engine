"""Lightweight visual aesthetic metrics (lighting + composition basics).

These are deliberately simple cv2/numpy heuristics for Phase 2 — exposure,
sharpness, contrast, colorfulness — all normalized to [0, 1]. The real aesthetic
brain is OpenCLIP (Phase 5), which will augment or supersede these. Frames are
sampled at ``sample_rate_hz``. Unknown metric names in config are ignored, so
Phase 5 can add openclip-backed metrics without breaking this module.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel

from ..config import AestheticsConfig
from ..utils.fileio import ensure_dir, write_json

SHARPNESS_NORM = 1000.0  # variance-of-Laplacian scale: blurry ~<100, crisp ~>1000
COLORFULNESS_NORM = 100.0  # Hasler-Süsstrunk colorfulness scale


class AestheticsAnalysis(BaseModel):
    """Per-frame visual metrics for one source, written to aesthetics.json."""

    duration_sec: float
    sample_rate_hz: float
    times_sec: list[float]
    metrics: dict[str, list[float]]
    means: dict[str, float]
    frame_count: int


def lighting_quality(gray: np.ndarray) -> float:
    """Exposure quality: 1.0 at mid-grey, falling off toward crushed/blown frames."""
    brightness = float(gray.mean()) / 255.0
    return float(1.0 - 2.0 * abs(brightness - 0.5))


def composition_score(gray: np.ndarray) -> float:
    """Contrast/dynamic-range proxy (placeholder until OpenCLIP composition scoring)."""
    return float(min(1.0, float(gray.std()) / 128.0))


def sharpness(gray: np.ndarray) -> float:
    """Focus measure: normalized variance of the Laplacian."""
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return float(min(1.0, variance / SHARPNESS_NORM))


def colorfulness(frame_bgr: np.ndarray) -> float:
    """Hasler-Süsstrunk colorfulness, normalized to [0, 1]."""
    b, g, r = (frame_bgr[..., i].astype(np.float64) for i in range(3))
    rg = r - g
    yb = 0.5 * (r + g) - b
    std = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2))
    mean = float(np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    return float(min(1.0, (std + 0.3 * mean) / COLORFULNESS_NORM))


def frame_aesthetics(frame_bgr: np.ndarray, metric_names: list[str]) -> dict[str, float]:
    """Compute the requested metrics for one BGR frame. Unknown names are ignored."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    out: dict[str, float] = {}
    for name in metric_names:
        if name == "lighting_quality":
            out[name] = lighting_quality(gray)
        elif name == "composition_score":
            out[name] = composition_score(gray)
        elif name == "sharpness":
            out[name] = sharpness(gray)
        elif name == "color_harmony":
            out[name] = colorfulness(frame_bgr)
    return out


def analyze_aesthetics(video_path: Path, config: AestheticsConfig) -> AestheticsAnalysis:
    """Sample frames and compute the configured visual metrics over ``video_path``."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():  # pragma: no cover - upstream validator rejects bad video
        cap.release()
        raise ValueError(f"cannot open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    step = max(1, round(fps / config.sample_rate_hz))

    times: list[float] = []
    series: dict[str, list[float]] = {name: [] for name in config.metrics}
    idx = 0
    while cap.grab():
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:  # pragma: no cover - defensive
                break
            for name, value in frame_aesthetics(frame, config.metrics).items():
                series[name].append(value)
            times.append(idx / fps)
        idx += 1
    cap.release()

    means = {name: (float(np.mean(values)) if values else 0.0) for name, values in series.items()}
    return AestheticsAnalysis(
        duration_sec=idx / fps if fps else 0.0,
        sample_rate_hz=config.sample_rate_hz,
        times_sec=times,
        metrics=series,
        means=means,
        frame_count=len(times),
    )


def write_aesthetics_json(analysis: AestheticsAnalysis, analysis_dir: Path) -> Path:
    """Write the analysis to ``<analysis_dir>/aesthetics.json``; return its path."""
    out = ensure_dir(analysis_dir) / "aesthetics.json"
    write_json(out, analysis.model_dump())
    return out
