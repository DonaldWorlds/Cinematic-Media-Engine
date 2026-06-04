"""Aspect-ratio reframing filtergraphs (9:16 / 1:1 / 16:9).

Pure ffmpeg ``-filter_complex`` builders, each producing a ``[vout]`` label.

- ``blur_pad`` (DEFAULT): FIT the whole source inside the target (no crop) over a
  blurred, cover-scaled copy of itself — keeps all content (full bodies, edge UI,
  graphics), TikTok-style. Uses ``force_original_aspect_ratio=decrease`` so any
  source aspect (16:9, tall, square) fits gracefully instead of overflowing.
- ``center_crop``: scale to COVER then centre-crop — full-bleed but LOSSY on
  non-centred content (cover-scales ~1.78x and crops the sides, chopping subjects
  / edge graphics). Kept selectable; not the default.

Face-aware crop is future work (needs face detection — Phase 5+). See KNOWLEDGE.md.
"""

from __future__ import annotations

from ..utils.logging import get_logger
from .backend import TrackCrop

_log = get_logger(__name__)

CENTER_CROP = "center_crop"
BLUR_PAD = "blur_pad"
TRACK = "track"
ZOOM_CROP = "zoom_crop"
BLUR_SIGMA = 20  # gaussian blur strength for the cover background
# blur_pad foreground scale: 1.0 = full fit (big bars); >1.0 enlarges the sharp band
# (smaller bars, slight L/R crop). Capped at the 16:9->9:16 full-bleed point — beyond
# that a 16:9 source is effectively center_crop. Non-16:9 sources reach full-bleed at a
# different point, but 3.16 is a safe ceiling (see KNOWLEDGE.md).
FOREGROUND_SCALE_MAX = 3.16


def clamp_foreground_scale(foreground_scale: float) -> float:
    """Clamp to [1.0, FOREGROUND_SCALE_MAX] (never shrink below full fit; cap at full-bleed)."""
    return max(1.0, min(foreground_scale, FOREGROUND_SCALE_MAX))


def foreground_fit_box(width: int, height: int, foreground_scale: float) -> tuple[int, int]:
    """The (even) box the sharp foreground fits inside = frame scaled by ``foreground_scale``."""
    return 2 * round(width * foreground_scale / 2), 2 * round(height * foreground_scale / 2)


def zoom_crop_box(width: int, height: int, foreground_scale: float) -> tuple[int, int]:
    """The (even) cover box for zoom_crop = target scaled by clamped ``foreground_scale``.

    Covering this box (then centre-cropping to target) punches in by the scale factor;
    fs=1.0 yields the plain center_crop cover box (no extra zoom).
    """
    fs = clamp_foreground_scale(foreground_scale)
    return 2 * round(width * fs / 2), 2 * round(height * fs / 2)


def compute_zoom_crop_x(
    *,
    subject_x_source: float,
    source_w: int,
    source_h: int,
    target_w: int,
    target_h: int,
    foreground_scale: float,
) -> int:
    """Scaled-space crop-x that centres the zoom_crop on a subject at ``subject_x_source``.

    zoom_crop cover-scales the source by ``foreground_scale`` (so it covers the zoom box)
    THEN crops the target, so the subject's source-x is mapped through the cover-scale
    factor before centring. The result is clamped so the crop stays inside the scaled
    image (subject stays visible, off-centre, at the edges).
    """
    zoom_w, zoom_h = zoom_crop_box(target_w, target_h, foreground_scale)
    scale = max(zoom_w / source_w, zoom_h / source_h)
    scaled_w = source_w * scale
    crop_x = subject_x_source * scale - target_w / 2
    return round(min(max(crop_x, 0.0), scaled_w - target_w))


def fit_inside(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int, int, int]:
    """Fitted ``(w, h, x, y)`` to place a source inside ``dst`` keeping all content.

    Scales by the smaller ratio (so the whole frame fits), rounds to even dimensions
    (h264 requirement), and centres. e.g. 1920x1080 -> (1080, 608, 0, 656).
    """
    scale = min(dst_w / src_w, dst_h / src_h)
    w = 2 * round(src_w * scale / 2)
    h = 2 * round(src_h * scale / 2)
    return w, h, (dst_w - w) // 2, (dst_h - h) // 2


def reframe_filtergraph(
    method: str,
    width: int,
    height: int,
    foreground_scale: float = 1.0,
    track_crop: TrackCrop | None = None,
    zoom_crop_x: int | None = None,
) -> str:
    """Return a ``-filter_complex`` chain (consuming ``[0:v]``, producing ``[vout]``).

    ``foreground_scale`` enlarges the sharp band for blur_pad (fit inside a scaled box,
    centred overlay clips L/R overflow) and is the cover punch-in for zoom_crop (full-bleed,
    no bars). Ignored by center_crop. ``track`` crops a full-height 9:16 window whose x
    follows ``track_crop.x_expr``. ``zoom_crop_x`` (zoom_crop only) sets the crop's x in
    scaled space to centre on a subject; ``None`` keeps ffmpeg's geometric centre.
    """
    if method == TRACK and track_crop is not None:
        # y=0 (named, not positional): full-height crop, so y is always the top. ffmpeg
        # rejects a positional y after the named x= option ("No option name near '0'").
        return (
            f"[0:v]crop={track_crop.crop_w}:{track_crop.crop_h}:x='{track_crop.x_expr}':y=0,"
            f"scale={width}:{height}[vout]"
        )
    if method == ZOOM_CROP:
        zw, zh = zoom_crop_box(width, height, foreground_scale)
        crop = f"crop={width}:{height}"
        if zoom_crop_x is not None:
            # name x and y (like the track branch) so ffmpeg doesn't read a positional
            # value; y stays the geometric vertical centre, only x follows the subject.
            crop = f"crop={width}:{height}:x={zoom_crop_x}:y=(ih-oh)/2"
        return f"[0:v]scale={zw}:{zh}:force_original_aspect_ratio=increase,{crop}[vout]"
    if method == CENTER_CROP:
        return (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}[vout]"
        )
    if method != BLUR_PAD:
        _log.warning("unknown reframe_method %r; falling back to blur_pad", method)
    fg_w, fg_h = foreground_fit_box(width, height, clamp_foreground_scale(foreground_scale))
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={BLUR_SIGMA}[bgb];"
        f"[fg]scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease[fgf];"
        f"[bgb][fgf]overlay=(W-w)/2:(H-h)/2[vout]"
    )
