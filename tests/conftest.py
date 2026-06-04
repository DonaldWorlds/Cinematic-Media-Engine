"""Shared pytest fixtures.

The sample video is synthetic and generated on demand by ffmpeg so it never
needs to be committed (see .gitignore). Regenerating it is deterministic, so
any machine running the suite produces the same fixture.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_VIDEO = FIXTURES_DIR / "sample_60s_1080p.mp4"
SAMPLE_AUDIO = FIXTURES_DIR / "sample_60s_16k_mono.wav"
SAMPLE_DURATION_SEC = 60
SAMPLE_WIDTH = 1920
SAMPLE_HEIGHT = 1080
SAMPLE_FPS = 30
SAMPLE_AUDIO_RATE = 16000


def _generate_sample_video(dest: Path) -> None:
    """Render a 60s 1080p30 test pattern with a 440 Hz sine tone."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={SAMPLE_WIDTH}x{SAMPLE_HEIGHT}:rate={SAMPLE_FPS}:duration={SAMPLE_DURATION_SEC}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={SAMPLE_DURATION_SEC}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"fixture ffmpeg failed: {result.stderr[-800:]}")


@pytest.fixture(scope="session")
def sample_video() -> Path:
    """Path to the synthetic 60s 1080p sample video, generated if missing."""
    if not SAMPLE_VIDEO.exists():
        _generate_sample_video(SAMPLE_VIDEO)
    return SAMPLE_VIDEO


@pytest.fixture(scope="session")
def sample_audio(sample_video: Path) -> Path:
    """16 kHz mono WAV extracted once from the sample video (generated if missing).

    Reuses the Phase-1 ingestion extractor so the analysis layer is tested
    against exactly the audio the real pipeline feeds it.
    """
    if not SAMPLE_AUDIO.exists():
        from src.ingestion.audio_extract import extract_audio

        extract_audio(sample_video, SAMPLE_AUDIO, sample_rate=SAMPLE_AUDIO_RATE, mono=True)
    return SAMPLE_AUDIO


@pytest.fixture
def non_video_file(tmp_path: Path) -> Path:
    """A text file masquerading as media — must be rejected by the validator."""
    p = tmp_path / "not_a_video.mp4"
    p.write_text("this is not a video")
    return p
