"""Emotional-moment detection — audio-dynamics proxy (Phase 3 interim).

There is no face/expression model in Phase 2 and OpenCLIP/TwelveLabs are Phase 5
/ optional, so the cross-mode substrate for "emotional intensity" is audio
dynamics: laughter, exclamations, hype and emotional swells show up as the RATE
OF CHANGE of energy. ``emotional_timeline`` is the |ΔRMS| envelope resampled onto
the scoring grid and min-max normalized — distinct from ``audio_peaks`` (discrete
peak density) and ``speech_intensity`` (band level), and requiring no transcript
so cinematic (default no-transcribe) gets a real signal too.

This is an INTERIM proxy with the same status as the cv2 aesthetics placeholders;
Phase 5 face/OpenCLIP/TwelveLabs emotion supersedes it. Raw first-difference
magnitude, no smoothing in v1 (see KNOWLEDGE.md). Feeds shortform
``emotional_reactions`` and cinematic ``emotional_moments``.
"""

from __future__ import annotations

from collections.abc import Sequence

from .scoring import normalize, resample_curve


def emotional_timeline(
    times: Sequence[float], energy: Sequence[float], grid: Sequence[float]
) -> list[float]:
    """Min-max-normalized |ΔRMS| envelope on ``grid`` (flat/empty -> zeros)."""
    resampled = resample_curve(times, energy, grid)
    deltas = [0.0] + [abs(resampled[i] - resampled[i - 1]) for i in range(1, len(resampled))]
    return normalize(deltas)
