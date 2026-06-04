"""Time conversion helpers. Timestamps are float seconds end-to-end."""

from __future__ import annotations


def seconds_to_hms(seconds: float) -> str:
    """Format float seconds as ``HH:MM:SS.mmm`` (millisecond precision)."""
    if seconds < 0:
        raise ValueError(f"seconds must be non-negative, got {seconds}")
    total_ms = round(seconds * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def hms_to_seconds(timestamp: str) -> float:
    """Parse ``HH:MM:SS.mmm`` (or ``HH:MM:SS``) back to float seconds."""
    parts = timestamp.split(":")
    if len(parts) != 3:
        raise ValueError(f"expected HH:MM:SS[.mmm], got {timestamp!r}")
    hours, minutes, secs = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)
