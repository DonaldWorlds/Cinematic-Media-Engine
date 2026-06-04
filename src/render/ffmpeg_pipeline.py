"""FFmpeg render backend — the first concrete :class:`RenderBackend`.

This is the ONLY module that shells out to ffmpeg. It composes the reframe filter
(social_formats), burns captions (subtitles), optionally overlays a watermark, and
encodes each clip. ``build_clip_command`` is a pure arg builder (unit-tested);
``FFmpegBackend.render_clip`` writes the ASS file, runs ffmpeg, and falls back from
the hardware encoder to software (libx264) if the GPU path fails (SPEC §9).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..utils.ffmpeg import FFmpegError, run_ffmpeg
from ..utils.fileio import ensure_dir
from ..utils.logging import get_logger
from .backend import ClipRenderSpec
from .social_formats import reframe_filtergraph
from .subtitles import build_ass, resolve_font

_log = get_logger(__name__)

DEFAULT_ENCODER = "h264_videotoolbox"  # M1 hardware H.264 (~3x faster)
FALLBACK_ENCODER = "libx264"  # software fallback if videotoolbox fails


def build_clip_command(
    spec: ClipRenderSpec,
    *,
    reframe_graph: str,
    subtitle_path: Path | None,
    encoder: str,
    subtitle_fontsdir: Path | None = None,
) -> list[str]:
    """Build the ffmpeg args (sans binary) to cut, reframe, caption and encode one clip.

    Fast input seek to ``start_sec`` then ``-t`` for the duration. ``reframe_graph`` is
    a ``-filter_complex`` chain producing ``[vout]`` (e.g. blur_pad's split/overlay);
    the caption burn is appended onto ``[vout]`` -> ``[vsub]`` when present. The video
    output and (optional) source audio are explicitly mapped.
    """
    filter_complex = reframe_graph
    video_label = "vout"
    if subtitle_path is not None:
        burn = f"subtitles=filename='{subtitle_path}'"
        if subtitle_fontsdir is not None:
            burn += f":fontsdir='{subtitle_fontsdir}'"
        filter_complex = f"{reframe_graph};[vout]{burn}[vsub]"
        video_label = "vsub"

    duration = spec.end_sec - spec.start_sec
    args = [
        "-y",
        "-ss",
        f"{spec.start_sec:.3f}",
        "-i",
        str(spec.source),
        "-t",
        f"{duration:.3f}",
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{video_label}]",
        "-map",
        "0:a?",  # optional source audio (no failure if the source has none)
        "-r",
        str(spec.fmt.fps),
        "-c:v",
        encoder,
    ]
    if spec.fmt.bitrate:
        args += ["-b:v", spec.fmt.bitrate]
    args += ["-c:a", spec.fmt.audio_codec, "-movflags", "+faststart", str(spec.output_path)]
    return args


class FFmpegBackend:
    """Concrete :class:`RenderBackend` that renders clips with ffmpeg."""

    def __init__(
        self, *, encoder: str = DEFAULT_ENCODER, fallback_encoder: str = FALLBACK_ENCODER
    ) -> None:
        self.encoder = encoder
        self.fallback_encoder = fallback_encoder

    def render_clip(self, spec: ClipRenderSpec) -> Path:
        """Render one clip (cut -> reframe -> burn captions -> encode) and return its path."""
        ensure_dir(spec.output_path.parent)
        reframe_graph = reframe_filtergraph(
            spec.reframe_method,
            spec.fmt.width,
            spec.fmt.height,
            spec.foreground_scale,
            spec.track_crop,
            zoom_crop_x=spec.zoom_crop_x,
        )

        if spec.watermark_path is not None and not spec.watermark_path.exists():
            # Expected: watermark asset intentionally absent (branding deferred to Descript,
            # see KNOWLEDGE.md). DEBUG, not per-clip noise.
            _log.debug("watermark %s missing; skipping watermark overlay", spec.watermark_path)

        ass_path: Path | None = None
        fontsdir: Path | None = None
        if spec.captions:
            font_name, fontsdir, _ = resolve_font(spec.caption_style.font)
            ass = build_ass(
                spec.captions,
                width=spec.fmt.width,
                height=spec.fmt.height,
                font_name=font_name,
                font_size=spec.caption_style.font_size,
                primary_color=spec.caption_style.primary_color,
                outline_color=spec.caption_style.outline_color,
                outline=spec.caption_style.outline,
                margin_v=spec.caption_style.margin_v,
                margin_l=spec.caption_style.margin_l,
                margin_r=spec.caption_style.margin_r,
            )
            fd, name = tempfile.mkstemp(suffix=".ass")
            os.close(fd)  # we write via Path below, not the descriptor
            ass_path = Path(name)
            ass_path.write_text(ass, encoding="utf-8")

        try:
            self._encode(spec, reframe_graph, ass_path, fontsdir)
        finally:
            if ass_path is not None:
                ass_path.unlink(missing_ok=True)
        return spec.output_path

    def _encode(
        self,
        spec: ClipRenderSpec,
        reframe_graph: str,
        ass_path: Path | None,
        fontsdir: Path | None,
    ) -> None:
        args = build_clip_command(
            spec,
            reframe_graph=reframe_graph,
            subtitle_path=ass_path,
            encoder=self.encoder,
            subtitle_fontsdir=fontsdir,
        )
        try:
            run_ffmpeg(args)
        except FFmpegError as err:
            if self.encoder == self.fallback_encoder:
                raise
            _log.warning(
                "encoder %s failed (%s); retrying with %s",
                self.encoder,
                err,
                self.fallback_encoder,
            )
            fallback_args = build_clip_command(
                spec,
                reframe_graph=reframe_graph,
                subtitle_path=ass_path,
                encoder=self.fallback_encoder,
                subtitle_fontsdir=fontsdir,
            )
            run_ffmpeg(fallback_args)
