"""OpenCV + PySceneDetect video analysis: motion intensity and scene cuts.

Feeds the visual-pacing signals: ``motion`` (shortform fast_motion, pacing) and
``scene_cuts_sec`` (segment boundaries for clustering/pacing). Frames are sampled
at ``motion_sample_rate_hz`` rather than decoded densely — pacing needs a coarse
curve, not every frame.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel
from scenedetect import ContentDetector, detect

from ..config import OpenCVConfig
from ..utils.fileio import ensure_dir, write_json


class OpenCVAnalysis(BaseModel):
    """Motion curve and scene-cut boundaries for one source, for opencv.json."""

    duration_sec: float
    sample_rate_hz: float
    motion_times_sec: list[float]
    motion: list[float]
    mean_motion: float
    max_motion: float
    scene_cuts_sec: list[float]
    scene_count: int


def frame_motion(prev_gray: np.ndarray, curr_gray: np.ndarray) -> float:
    """Mean absolute difference between two grayscale frames, normalized to [0, 1]."""
    diff = np.abs(curr_gray.astype(np.float64) - prev_gray.astype(np.float64))
    return float(diff.mean()) / 255.0


def _motion_curve(
    video_path: Path, sample_rate_hz: float
) -> tuple[list[float], list[float], float]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():  # pragma: no cover - upstream validator rejects bad video
        cap.release()
        raise ValueError(f"cannot open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    step = max(1, round(fps / sample_rate_hz))

    times: list[float] = []
    motion: list[float] = []
    prev_gray: np.ndarray | None = None
    idx = 0
    while cap.grab():  # grab() advances without decoding; retrieve() only on samples
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:  # pragma: no cover - defensive
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                motion.append(frame_motion(prev_gray, gray))
                times.append(idx / fps)
            prev_gray = gray
        idx += 1
    cap.release()
    duration = idx / fps if fps else 0.0
    return times, motion, duration


def analyze_video(video_path: Path, config: OpenCVConfig) -> OpenCVAnalysis:
    """Sample motion intensity and detect scene cuts in ``video_path``."""
    times, motion, duration = _motion_curve(video_path, config.motion_sample_rate_hz)

    scenes = detect(str(video_path), ContentDetector(threshold=config.scene_detect_threshold))
    scene_cuts = [float(start.get_seconds()) for start, _ in scenes[1:]]

    return OpenCVAnalysis(
        duration_sec=duration,
        sample_rate_hz=config.motion_sample_rate_hz,
        motion_times_sec=times,
        motion=motion,
        mean_motion=float(np.mean(motion)) if motion else 0.0,
        max_motion=float(np.max(motion)) if motion else 0.0,
        scene_cuts_sec=scene_cuts,
        scene_count=max(1, len(scenes)),
    )


def write_opencv_json(analysis: OpenCVAnalysis, analysis_dir: Path) -> Path:
    """Write the analysis to ``<analysis_dir>/opencv.json``; return its path."""
    out = ensure_dir(analysis_dir) / "opencv.json"
    write_json(out, analysis.model_dump())
    return out
