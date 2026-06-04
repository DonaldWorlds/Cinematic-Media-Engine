"""WhisperX transcription: faster-whisper transcript + wav2vec2 forced alignment.

Locked config (see KNOWLEDGE.md / SPEC §11): faster-whisper transcription runs
CPU-only on M1 (CTranslate2 has no Metal backend); the wav2vec2 alignment pass
runs on MPS via torch. word_timestamps come from alignment, not model size, so
``small`` is sufficient. Diarization is off (single creator, no HF token).

WhisperX is imported lazily inside ``transcribe_audio`` so the transcript-parsing
logic (``build_transcript``) stays importable and testable without the ~800MB
model stack.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import TranscriptionConfig
from ..utils.fileio import write_json
from ..utils.logging import get_logger

_log = get_logger(__name__)

# Override the alignment device at runtime: cpu | mps | cuda | auto. Default falls
# back to the config (`transcription.alignment.device`, itself "auto"). See KNOWLEDGE.md.
ALIGNMENT_DEVICE_ENV = "CME_ALIGNMENT_DEVICE"


class Word(BaseModel):
    """A single aligned word. start/end/score are None before alignment."""

    word: str
    start: float | None = None
    end: float | None = None
    score: float | None = None


class Segment(BaseModel):
    """A transcript segment with its words."""

    start: float
    end: float
    text: str
    words: list[Word] = []


class TranscriptResult(BaseModel):
    """Full transcript for one source, written to the workspace transcript.json."""

    language: str
    text: str
    segments: list[Segment]
    word_count: int
    aligned: bool


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_transcript(raw: dict[str, Any], *, language: str, aligned: bool) -> TranscriptResult:
    """Normalize a raw WhisperX result dict into a TranscriptResult."""
    segments: list[Segment] = []
    for seg in raw.get("segments", []):
        words = [
            Word(
                word=str(w.get("word", "")).strip(),
                start=_opt_float(w.get("start")),
                end=_opt_float(w.get("end")),
                score=_opt_float(w.get("score")),
            )
            for w in seg.get("words", [])
        ]
        segments.append(
            Segment(
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                text=str(seg.get("text", "")).strip(),
                words=words,
            )
        )
    return TranscriptResult(
        language=language,
        text=" ".join(s.text for s in segments).strip(),
        segments=segments,
        word_count=sum(len(s.words) for s in segments),
        aligned=aligned,
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


def _select_alignment_device(config_device: str) -> str:
    """Resolve the alignment device: ``CME_ALIGNMENT_DEVICE`` overrides the config."""
    return _resolve_device(os.environ.get(ALIGNMENT_DEVICE_ENV, config_device))


def align_transcript(
    segments: list[dict[str, Any]],
    *,
    audio: Any,
    language_code: str,
    device: str,
    load_align_model: Callable[..., tuple[Any, Any]],
    align: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Run wav2vec2 alignment on ``device``, falling back to CPU on a GPU-op failure.

    MPS (and CUDA) can raise ``NotImplementedError``/``RuntimeError`` for ops or
    tensor sizes their backend doesn't support — e.g. wav2vec2's conv1d on a long
    audio segment exceeds MPS's limit ("Output channels > 65536"). Per SPEC §9 we
    log a warning and retry the whole alignment on CPU. Returns the aligned result
    and the device that actually produced it. A CPU failure propagates (real error).
    """
    _log.info("alignment: device=%s", device)
    try:
        model, metadata = load_align_model(language_code=language_code, device=device)
        raw = align(segments, model, metadata, audio, device, return_char_alignments=False)
        return raw, device
    except (NotImplementedError, RuntimeError) as err:
        if device == "cpu":
            raise
        _log.warning("alignment on %s failed (%s); retrying on CPU", device, err)
        model, metadata = load_align_model(language_code=language_code, device="cpu")
        raw = align(segments, model, metadata, audio, "cpu", return_char_alignments=False)
        return raw, "cpu"


def transcribe_audio(audio_path: Path, config: TranscriptionConfig) -> TranscriptResult:
    """Transcribe ``audio_path`` with WhisperX and optionally align word timestamps."""
    import whisperx

    audio = whisperx.load_audio(str(audio_path), sr=config.audio_preprocess.sample_rate)
    asr_options = {
        "beam_size": config.beam_size,
        "condition_on_previous_text": config.condition_on_previous_text,
    }
    vad_options = (
        {"min_silence_duration_ms": config.vad_min_silence_ms} if config.vad_filter else None
    )
    model = whisperx.load_model(
        config.model_size,
        device=config.device,  # cpu on M1 — CTranslate2 has no Metal backend
        compute_type=config.compute_type,
        language=config.language,
        asr_options=asr_options,
        vad_options=vad_options,
    )
    raw = model.transcribe(audio, language=config.language)

    aligned = False
    if config.alignment.enabled and raw.get("segments"):
        align_device = _select_alignment_device(config.alignment.device)  # mps on M1, env override
        raw, _ = align_transcript(
            raw["segments"],
            audio=audio,
            language_code=config.language,
            device=align_device,
            load_align_model=whisperx.load_align_model,
            align=whisperx.align,
        )
        aligned = True

    return build_transcript(raw, language=config.language, aligned=aligned)


def write_transcript_json(result: TranscriptResult, dest: Path) -> Path:
    """Write the transcript to ``dest`` (the workspace transcript.json); return it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_json(dest, result.model_dump())
    return dest
