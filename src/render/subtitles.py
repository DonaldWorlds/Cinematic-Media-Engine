"""Caption generation: transcript words -> grouped ASS subtitle file for burn-in.

v1 burns grouped pop-on lines (<= max_words_per_line words, timed from word
start/end). True per-word karaoke highlight is a deferred refinement (KNOWLEDGE.md
Future Enhancements). Fonts: the configured brand font is used if present, else a
system font fallback (with a warning), per SPEC §9 missing-asset handling.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..utils.logging import get_logger
from .backend import CaptionLine

_log = get_logger(__name__)

# Tried in order when the brand font is absent (macOS system fonts).
SYSTEM_FONT_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("Helvetica", "/System/Library/Fonts/Helvetica.ttc"),
    ("Arial", "/System/Library/Fonts/Supplemental/Arial.ttf"),
)

# Captions are bottom-centre anchored (ASS alignment 2) and positioned via MarginV,
# so they land in the composition caption_zone, clear of the bottom platform UI.
_CAPTION_ALIGNMENT = 2


def caption_layout(
    caption_y_position: float, caption_safe_width: int, width: int, height: int
) -> tuple[int, int, int]:
    """Composition caption params -> ASS ``(margin_v, margin_l, margin_r)`` in px.

    ``margin_v`` is the gap from the bottom edge (bottom-anchored), so a higher
    ``caption_y_position`` sits the captions further down; ``margin_l/r`` centre the
    text within ``caption_safe_width``.
    """
    margin_v = round(height * (1.0 - caption_y_position))
    margin_lr = round((width - caption_safe_width) / 2)
    return margin_v, margin_lr, margin_lr


def group_words_into_lines(
    words: Sequence[tuple[str, float, float]], max_words: int
) -> list[CaptionLine]:
    """Group consecutive ``(text, start, end)`` words into <=``max_words`` caption lines."""
    lines: list[CaptionLine] = []
    for i in range(0, len(words), max_words):
        chunk = words[i : i + max_words]
        lines.append(
            CaptionLine(
                start_sec=chunk[0][1],
                end_sec=chunk[-1][2],
                text=" ".join(w[0] for w in chunk),
            )
        )
    return lines


def resolve_font(configured: Path | None) -> tuple[str, Path | None, bool]:
    """Resolve the caption font family.

    Returns ``(font_name, fontsdir, is_fallback)``. Uses the brand font if the file
    exists (its directory as fontsdir so libass can find it); otherwise falls back to
    a system font with a warning.
    """
    if configured is not None and configured.exists():
        return configured.stem, configured.parent, False
    for name, path in SYSTEM_FONT_FALLBACKS:
        if Path(path).exists():
            # Expected: brand font is intentionally absent (branding deferred to Descript,
            # see KNOWLEDGE.md). Helvetica fallback is acceptable -> DEBUG, not per-clip noise.
            _log.debug(
                "caption font %s missing; falling back to system font %r",
                configured,
                name,
            )
            return name, None, True
    _log.warning("no caption font found (brand or system); captions may not render")
    return "sans-serif", None, True


def _ass_time(seconds: float) -> str:
    """Format seconds as an ASS timestamp ``H:MM:SS.cc`` (centiseconds)."""
    total_cs = round(max(0.0, seconds) * 100)
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(rgb_hex: str) -> str:
    """Convert ``RRGGBB`` to ASS ``&HAABBGGRR`` (opaque)."""
    rr, gg, bb = rgb_hex[0:2], rgb_hex[2:4], rgb_hex[4:6]
    return f"&H00{bb}{gg}{rr}".upper()


def build_ass(
    lines: Sequence[CaptionLine],
    *,
    width: int,
    height: int,
    font_name: str,
    font_size: int,
    primary_color: str,
    outline_color: str,
    outline: int,
    margin_v: int,
    margin_l: int,
    margin_r: int,
) -> str:
    """Build a complete ASS subtitle document for ``lines`` (clip-relative timing).

    Captions are bottom-centre anchored; ``margin_v`` lifts them into the caption
    zone and ``margin_l/r`` constrain them to the safe width (see ``caption_layout``).
    """
    style = (
        f"Style: Default,{font_name},{font_size},{_ass_color(primary_color)},"
        f"&H000000FF,{_ass_color(outline_color)},&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},0,"
        f"{_CAPTION_ALIGNMENT},{margin_l},{margin_r},{margin_v},1"
    )
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        f"Dialogue: 0,{_ass_time(line.start_sec)},{_ass_time(line.end_sec)},Default,,0,0,0,,"
        f"{line.text}"
        for line in lines
    ]
    return "\n".join([*header, *events]) + "\n"
