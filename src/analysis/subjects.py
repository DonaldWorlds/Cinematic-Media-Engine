"""Subject detection (Phase 4.5, Layer 1) — YOLO person detection -> content profile.

Samples source frames, detects people (YOLO ``person`` class) and writes a per-frame
content profile to ``analysis/subjects.json``: how many subjects, their union bbox,
and how wide they span the frame. Layer 2 (auto-reframe decision) reads this to pick
the reframe method + foreground_scale per clip.

The profile math (count / union / spread / is_solo) is pure and unit-tested. YOLO is
lazy-imported inside ``detect_subjects`` (heavy torch stack) so the math stays testable
without the model. Runs sequentially in-process (cv2+av libavdevice clash — see
KNOWLEDGE.md), MPS with CPU fallback like the wav2vec2 alignment step.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import cv2
from pydantic import BaseModel

from ..config import SubjectsConfig
from ..utils.fileio import ensure_dir, write_json
from ..utils.logging import get_logger

_log = get_logger(__name__)

PERSON_CLASS_ID = 0  # COCO "person" class


class SubjectBox(BaseModel):
    """One detected person's pixel bounding box."""

    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float


class FrameSubjects(BaseModel):
    """The content profile for one sampled frame."""

    time_sec: float
    subject_count: int
    spread_ratio: float
    is_solo: bool
    group_bbox: list[float] | None
    boxes: list[SubjectBox]


class SubjectsAnalysis(BaseModel):
    """Per-frame subject detections for one source, written to subjects.json."""

    duration_sec: float
    sample_rate_hz: float
    frame_width: int
    frame_height: int
    frames: list[FrameSubjects]


def union_bbox(boxes: Sequence[SubjectBox]) -> tuple[float, float, float, float] | None:
    """Smallest box enclosing all ``boxes`` as ``(x1, y1, x2, y2)`` (None if empty)."""
    if not boxes:
        return None
    return (
        min(b.x1 for b in boxes),
        min(b.y1 for b in boxes),
        max(b.x2 for b in boxes),
        max(b.y2 for b in boxes),
    )


def spread_ratio(boxes: Sequence[SubjectBox], frame_width: int) -> float:
    """Fraction of frame width the subjects span (0.0 when none / zero-width frame)."""
    if not boxes or frame_width <= 0:
        return 0.0
    return (max(b.x2 for b in boxes) - min(b.x1 for b in boxes)) / frame_width


def profile_frame(boxes: Sequence[SubjectBox], frame_width: int, time_sec: float) -> FrameSubjects:
    """Build one frame's content profile from its detected person boxes."""
    bbox = union_bbox(boxes)
    return FrameSubjects(
        time_sec=time_sec,
        subject_count=len(boxes),
        spread_ratio=spread_ratio(boxes, frame_width),
        is_solo=len(boxes) == 1,
        group_bbox=list(bbox) if bbox is not None else None,
        boxes=list(boxes),
    )


def _resolve_device(device: str) -> str:
    if device == "auto":
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    return device


def _person_boxes(result: object, confidence: float) -> list[SubjectBox]:
    """Extract person boxes above ``confidence`` from one ultralytics result."""
    boxes_attr = getattr(result, "boxes", None)
    if boxes_attr is None:
        return []
    out: list[SubjectBox] = []
    for box in boxes_attr:
        cls = int(box.cls[0])
        conf = float(box.conf[0])
        if cls != PERSON_CLASS_ID or conf < confidence:
            continue
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
        out.append(SubjectBox(x1=x1, y1=y1, x2=x2, y2=y2, confidence=conf))
    return out


def detect_subjects(video_path: Path, config: SubjectsConfig) -> SubjectsAnalysis:
    """Sample frames from ``video_path`` and detect people with YOLO -> per-frame profiles."""
    from ultralytics import YOLO

    model = YOLO(config.model)
    device = _resolve_device(config.device)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():  # pragma: no cover - upstream validator rejects bad video
        cap.release()
        raise ValueError(f"cannot open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(fps / config.sample_rate_hz))

    frames: list[FrameSubjects] = []
    idx = 0
    while cap.grab():
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:  # pragma: no cover - defensive
                break
            boxes = _detect_one(model, frame, device, config.confidence)
            frames.append(profile_frame(boxes, width, idx / fps))
        idx += 1
    cap.release()

    return SubjectsAnalysis(
        duration_sec=idx / fps if fps else 0.0,
        sample_rate_hz=config.sample_rate_hz,
        frame_width=width,
        frame_height=height,
        frames=frames,
    )


def _detect_one(model: object, frame: object, device: str, confidence: float) -> list[SubjectBox]:
    """Run YOLO on one frame; fall back to CPU if the GPU device errors (SPEC §9)."""
    try:
        results = model.predict(  # type: ignore[attr-defined]
            frame, classes=[PERSON_CLASS_ID], conf=confidence, device=device, verbose=False
        )
    except (NotImplementedError, RuntimeError) as err:
        if device == "cpu":
            raise
        _log.warning("YOLO on %s failed (%s); retrying on CPU", device, err)
        results = model.predict(  # type: ignore[attr-defined]
            frame, classes=[PERSON_CLASS_ID], conf=confidence, device="cpu", verbose=False
        )
    return _person_boxes(results[0], confidence) if results else []


def write_subjects_json(analysis: SubjectsAnalysis, analysis_dir: Path) -> Path:
    """Write the analysis to ``<analysis_dir>/subjects.json``; return its path."""
    out = ensure_dir(analysis_dir) / "subjects.json"
    write_json(out, analysis.model_dump())
    return out
