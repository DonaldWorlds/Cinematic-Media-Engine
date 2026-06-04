"""librosa audio analysis: RMS energy curve, speech-band intensity, energy peaks.

Feeds the audio scoring signals: ``energy`` (standard_longform audio_energy),
``speech_intensity`` (shortform speech_intensity), and ``peaks`` (shortform
audio_peaks). Beat/drop detection for cinematic soundtrack-sync is intentionally
out of scope here — no config parameters exist for it yet (see KNOWLEDGE.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import librosa
import numpy as np
from pydantic import BaseModel

from ..config import AudioConfig
from ..utils.fileio import ensure_dir, write_json

SPEECH_BAND_HZ = (300.0, 3400.0)  # telephony speech band


class Peak(BaseModel):
    """A single energy peak: when it happens and how far above baseline it is."""

    time_sec: float
    rms: float
    z_score: float


class LibrosaAnalysis(BaseModel):
    """Frame-aligned audio signals for one source, written to librosa.json."""

    sample_rate: int
    duration_sec: float
    window_sec: float
    times_sec: list[float]
    energy: list[float]
    speech_intensity: list[float]
    peaks: list[Peak]
    mean_rms: float
    max_rms: float


def detect_peaks(
    rms: Sequence[float],
    times_sec: Sequence[float],
    *,
    threshold_std: float,
    min_gap_sec: float,
) -> list[Peak]:
    """Energy peaks > ``threshold_std`` above the mean, suppressing neighbours
    closer than ``min_gap_sec`` (the taller peak in a cluster wins).

    A flat signal (std ~= 0) yields no peaks, which also avoids div-by-zero.
    """
    values = np.asarray(rms, dtype=float)
    if values.size == 0:
        return []
    mean = float(values.mean())
    std = float(values.std())
    if std <= 1e-9:
        return []
    z = (values - mean) / std
    candidates = [i for i in range(values.size) if z[i] >= threshold_std]
    candidates.sort(key=lambda i: values[i], reverse=True)  # non-max suppression
    accepted: list[int] = []
    for i in candidates:
        if all(abs(times_sec[i] - times_sec[j]) >= min_gap_sec for j in accepted):
            accepted.append(i)
    accepted.sort(key=lambda i: times_sec[i])
    return [
        Peak(time_sec=float(times_sec[i]), rms=float(values[i]), z_score=float(z[i]))
        for i in accepted
    ]


def analyze_audio(audio_path: Path, config: AudioConfig) -> LibrosaAnalysis:
    """Compute the RMS energy curve, speech-band intensity, and energy peaks."""
    sr = config.sample_rate
    y, _ = librosa.load(str(audio_path), sr=sr, mono=True)  # resamples to sr; sr unchanged
    win = max(1, round(config.energy_window_sec * sr))
    hop = win  # non-overlapping windows keep energy/speech/peak frames aligned

    rms = librosa.feature.rms(y=y, frame_length=win, hop_length=hop, center=True)[0]
    times = librosa.frames_to_time(np.arange(rms.size), sr=sr, hop_length=hop)

    magnitude = np.abs(librosa.stft(y, n_fft=win, hop_length=hop, center=True))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=win)
    band = (freqs >= SPEECH_BAND_HZ[0]) & (freqs <= SPEECH_BAND_HZ[1])
    band_rms = (
        np.sqrt(np.mean(magnitude[band] ** 2, axis=0))
        if bool(band.any())
        else np.zeros(magnitude.shape[1])
    )

    n = int(min(rms.size, band_rms.size, times.size))
    rms, band_rms, times = rms[:n], band_rms[:n], times[:n]
    peak_band = float(band_rms.max()) if n else 0.0
    speech = band_rms / peak_band if peak_band > 0 else band_rms

    return LibrosaAnalysis(
        sample_rate=sr,
        duration_sec=float(librosa.get_duration(y=y, sr=sr)),
        window_sec=config.energy_window_sec,
        times_sec=[float(t) for t in times],
        energy=[float(v) for v in rms],
        speech_intensity=[float(v) for v in speech],
        peaks=detect_peaks(
            [float(v) for v in rms],
            [float(t) for t in times],
            threshold_std=config.peak_threshold_std,
            min_gap_sec=config.min_peak_gap_sec,
        ),
        mean_rms=float(rms.mean()) if n else 0.0,
        max_rms=float(rms.max()) if n else 0.0,
    )


def write_librosa_json(analysis: LibrosaAnalysis, analysis_dir: Path) -> Path:
    """Write the analysis to ``<analysis_dir>/librosa.json``; return its path."""
    out = ensure_dir(analysis_dir) / "librosa.json"
    write_json(out, analysis.model_dump())
    return out
