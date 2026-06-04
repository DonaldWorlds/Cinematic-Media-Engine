"""Extract analysis-ready audio from a source video."""

from __future__ import annotations

from pathlib import Path

from ..utils.ffmpeg import run_ffmpeg


def extract_audio(
    source: Path,
    dest: Path,
    *,
    sample_rate: int = 16000,
    mono: bool = True,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    """Extract audio from ``source`` to ``dest`` as PCM16 WAV (default 16kHz mono).

    16 kHz mono PCM is Whisper's native input format, so downstream
    transcription skips its own decode/resample pass.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1" if mono else "2",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    run_ffmpeg(args, binary=ffmpeg_binary)
    return dest
