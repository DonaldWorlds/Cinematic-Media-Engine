"""Phase 4 render-layer tests (written before implementation).

Pure builders (ffmpeg command, 9:16 reframe filtergraph, ASS-from-words, font
resolution) are unit-tested with hand-built inputs. The end-to-end clip render is
integration-tested against the synthetic 60s fixture (ffprobe asserts geometry +
duration + audio). ffmpeg calls are confined to the FFmpegBackend behind the
RenderBackend abstraction (Descript backend slots in later).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from src.utils.ffmpeg import ffprobe_json

# --------------------------------------------------------------------------- #
# ffmpeg_pipeline.build_clip_command — cut + reframe + encode arg builder
# --------------------------------------------------------------------------- #


def _maps(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "-map"]


def test_build_clip_command_uses_filter_complex_and_maps_video_and_audio() -> None:
    from src.render.backend import ClipRenderSpec, RenderFormat
    from src.render.ffmpeg_pipeline import build_clip_command

    spec = ClipRenderSpec(
        source=Path("in.mp4"),
        start_sec=10.0,
        end_sec=70.0,
        fmt=RenderFormat(width=1080, height=1920, fps=30, video_codec="h264", bitrate="6M"),
        output_path=Path("out.mp4"),
    )
    graph = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[vout]"
    args = build_clip_command(
        spec, reframe_graph=graph, subtitle_path=None, encoder="h264_videotoolbox"
    )

    assert args[0] == "-y"
    assert args[args.index("-ss") + 1] == "10.000"  # fast input seek to clip start
    assert args[args.index("-i") + 1] == "in.mp4"
    assert args[args.index("-t") + 1] == "60.000"  # duration = end - start
    assert args[args.index("-filter_complex") + 1] == graph
    assert "[vout]" in _maps(args)  # mapped composite video
    assert "0:a?" in _maps(args)  # optional source audio
    assert args[args.index("-c:v") + 1] == "h264_videotoolbox"
    assert args[args.index("-r") + 1] == "30"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[-1] == "out.mp4"


def test_build_clip_command_burns_subtitles_on_vout_chain() -> None:
    from src.render.backend import ClipRenderSpec, RenderFormat
    from src.render.ffmpeg_pipeline import build_clip_command

    spec = ClipRenderSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=60.0,
        fmt=RenderFormat(width=1080, height=1920, fps=30),
        output_path=Path("out.mp4"),
    )
    args = build_clip_command(
        spec,
        reframe_graph="[0:v]scale=1080:1920[vout]",
        subtitle_path=Path("/tmp/clip.ass"),
        encoder="libx264",
    )
    fc = args[args.index("-filter_complex") + 1]
    assert "[vout]subtitles=" in fc and "/tmp/clip.ass" in fc  # burn consumes the reframed video
    assert fc.strip().endswith("[vsub]")  # subtitled output label
    assert "[vsub]" in _maps(args)  # and it's what gets mapped


# --------------------------------------------------------------------------- #
# social_formats.reframe_filter — 9:16 reframe filterchain
# --------------------------------------------------------------------------- #


def test_fit_inside_16x9_into_9x16() -> None:
    from src.render.social_formats import fit_inside

    # full 16:9 fits to 1080 wide -> 608 tall (even), centred with ~656px bars
    assert fit_inside(1920, 1080, 1080, 1920) == (1080, 608, 0, 656)


def test_reframe_center_crop_graph_covers_and_crops() -> None:
    from src.render.social_formats import reframe_filtergraph

    assert (
        reframe_filtergraph("center_crop", 1080, 1920)
        == "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[vout]"
    )


def test_reframe_blur_pad_graph_fits_blurs_and_centers() -> None:
    from src.render.social_formats import reframe_filtergraph

    g = reframe_filtergraph("blur_pad", 1080, 1920)
    assert "split=2" in g  # bg + fg copies
    assert "force_original_aspect_ratio=decrease" in g  # sharp foreground FITS inside (no crop)
    assert "force_original_aspect_ratio=increase" in g and "crop=1080:1920" in g  # blurred cover bg
    assert "gblur=sigma=20" in g  # background blur
    assert "overlay=(W-w)/2:(H-h)/2" in g  # centre the sharp layer
    assert g.endswith("[vout]")


def test_reframe_unknown_method_falls_back_to_blur_pad_default() -> None:
    from src.render.social_formats import reframe_filtergraph

    assert reframe_filtergraph("nonsense", 1080, 1920) == reframe_filtergraph(
        "blur_pad", 1080, 1920
    )


def test_reframe_track_uses_moving_crop_then_scales() -> None:
    from src.render.backend import TrackCrop
    from src.render.social_formats import reframe_filtergraph

    tc = TrackCrop(crop_w=608, crop_h=1080, x_expr="if(lt(t,2.000),100.000,300.000)")
    g = reframe_filtergraph("track", 1080, 1920, track_crop=tc)
    # y=0 must be named (not positional ':0') — ffmpeg rejects a positional arg after x=
    assert g == ("[0:v]crop=608:1080:x='if(lt(t,2.000),100.000,300.000)':y=0,scale=1080:1920[vout]")


def test_foreground_fit_box_scales_even() -> None:
    from src.render.social_formats import foreground_fit_box

    assert foreground_fit_box(1080, 1920, 1.0) == (1080, 1920)  # full fit (current)
    assert foreground_fit_box(1080, 1920, 1.2) == (1296, 2304)  # bigger band


def test_clamp_foreground_scale_bounds() -> None:
    from src.render.social_formats import clamp_foreground_scale

    assert clamp_foreground_scale(1.2) == 1.2
    assert clamp_foreground_scale(0.5) == 1.0  # never below full fit
    assert clamp_foreground_scale(10.0) == 3.16  # capped at the 16:9 full-bleed point


def test_reframe_blur_pad_foreground_scale_enlarges_band() -> None:
    from src.render.social_formats import reframe_filtergraph

    g = reframe_filtergraph("blur_pad", 1080, 1920, foreground_scale=1.2)
    # sharp foreground now fits inside the 1.2x box (1296x2304) -> bigger band, smaller bars
    assert "scale=1296:2304:force_original_aspect_ratio=decrease" in g
    # background still covers the full frame
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" in g


def test_zoom_crop_box_scales_even() -> None:
    from src.render.social_formats import zoom_crop_box

    assert zoom_crop_box(1080, 1920, 1.0) == (1080, 1920)  # fs=1.0 == center_crop cover box
    assert zoom_crop_box(1080, 1920, 1.3) == (1404, 2496)  # punched in 1.3x (even dims)


def test_reframe_zoom_crop_scales_and_crops_no_blur() -> None:
    from src.render.social_formats import reframe_filtergraph

    g = reframe_filtergraph("zoom_crop", 1080, 1920, foreground_scale=1.3)
    # cover a 1.3x box then centre-crop to target -> full-bleed punch-in, no bars
    assert g == "[0:v]scale=1404:2496:force_original_aspect_ratio=increase,crop=1080:1920[vout]"
    assert "gblur" not in g  # no blurred background
    assert "split" not in g  # single chain, not the blur_pad compositor


def test_reframe_zoom_crop_clamps_below_one_to_center_crop() -> None:
    from src.render.social_formats import reframe_filtergraph

    # fs<1.0 clamps to 1.0 -> identical framing to center_crop (cover + crop, no extra zoom)
    assert reframe_filtergraph(
        "zoom_crop", 1080, 1920, foreground_scale=0.5
    ) == reframe_filtergraph("center_crop", 1080, 1920)


# --------------------------------------------------------------------------- #
# subtitles — word grouping, ASS generation, font resolution
# --------------------------------------------------------------------------- #


def test_group_words_into_lines_respects_max_words() -> None:
    from src.render.subtitles import group_words_into_lines

    words = [("a", 0.0, 0.5), ("b", 0.5, 1.0), ("c", 1.0, 1.5), ("d", 1.5, 2.0), ("e", 2.0, 2.5)]
    lines = group_words_into_lines(words, max_words=4)

    assert len(lines) == 2
    assert lines[0].text == "a b c d"
    assert lines[0].start_sec == 0.0
    assert lines[0].end_sec == 2.0
    assert lines[1].text == "e"
    assert lines[1].start_sec == 2.0
    assert lines[1].end_sec == 2.5


def test_group_words_empty_returns_empty() -> None:
    from src.render.subtitles import group_words_into_lines

    assert group_words_into_lines([], max_words=4) == []


def test_caption_layout_from_composition() -> None:
    from src.render.subtitles import caption_layout

    margin_v, margin_l, margin_r = caption_layout(
        caption_y_position=0.80, caption_safe_width=900, width=1080, height=1920
    )
    assert margin_v == 384  # height * (1 - 0.80) -> clears the bottom UI
    assert margin_l == 90  # (1080 - 900) / 2
    assert margin_r == 90


def test_build_ass_bottom_anchored_with_zone_margins() -> None:
    from src.render.backend import CaptionLine
    from src.render.subtitles import build_ass

    lines = [CaptionLine(start_sec=0.0, end_sec=2.0, text="hello world")]
    ass = build_ass(
        lines,
        width=1080,
        height=1920,
        font_name="Helvetica",
        font_size=64,
        primary_color="FFFFFF",
        outline_color="000000",
        outline=3,
        margin_v=384,
        margin_l=90,
        margin_r=90,
    )
    assert "[Script Info]" in ass
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "[V4+ Styles]" in ass
    assert "[Events]" in ass
    assert "Dialogue:" in ass
    assert "hello world" in ass
    assert "0:00:00.00" in ass  # ASS H:MM:SS.cc start
    assert "0:00:02.00" in ass  # end
    # bottom-centre alignment (2) + zone margins L,R,V in the Style line
    assert ",2,90,90,384,1" in ass


def test_resolve_font_uses_brand_font_when_present(tmp_path: Path) -> None:
    from src.render.subtitles import resolve_font

    brand = tmp_path / "CaptionBold.ttf"
    brand.write_bytes(b"x")
    name, fontsdir, fallback = resolve_font(brand)

    assert fallback is False
    assert name == "CaptionBold"
    assert fontsdir == tmp_path


def test_resolve_font_falls_back_to_system_when_missing(tmp_path: Path) -> None:
    from src.render.subtitles import resolve_font

    name, fontsdir, fallback = resolve_font(tmp_path / "nope.ttf")

    assert fallback is True
    assert name  # a system font family name
    assert fontsdir is None


def test_build_clip_command_includes_fontsdir_when_given() -> None:
    from src.render.backend import ClipRenderSpec, RenderFormat
    from src.render.ffmpeg_pipeline import build_clip_command

    spec = ClipRenderSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=60.0,
        fmt=RenderFormat(width=1080, height=1920, fps=30),
        output_path=Path("out.mp4"),
    )
    args = build_clip_command(
        spec,
        reframe_graph="[0:v]scale=1080:1920[vout]",
        subtitle_path=Path("/tmp/clip.ass"),
        encoder="libx264",
        subtitle_fontsdir=Path("/brand/fonts"),
    )
    fc = args[args.index("-filter_complex") + 1]
    assert "fontsdir=" in fc and "/brand/fonts" in fc


# --------------------------------------------------------------------------- #
# FFmpegBackend.render_clip — end-to-end clip render (integration)
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_ffmpeg_backend_renders_vertical_clip_with_audio(
    sample_video: Path, tmp_path: Path
) -> None:
    from src.render.backend import CaptionLine, ClipRenderSpec, RenderFormat
    from src.render.ffmpeg_pipeline import FFmpegBackend

    out = tmp_path / "clip.mp4"
    spec = ClipRenderSpec(
        source=sample_video,
        start_sec=0.0,
        end_sec=3.0,  # short clip keeps the test fast; geometry/audio is what matters
        fmt=RenderFormat(width=1080, height=1920, fps=30, bitrate="4M"),
        output_path=out,
        reframe_method="blur_pad",  # exercise the filter_complex compositor (default)
        captions=[CaptionLine(start_sec=0.0, end_sec=2.0, text="hello world")],
    )

    result = FFmpegBackend().render_clip(spec)

    assert result == out
    assert out.exists() and out.stat().st_size > 0
    probe = ffprobe_json(out)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    assert int(video["width"]) == 1080
    assert int(video["height"]) == 1920
    assert audio is not None  # source audio preserved
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.7


# --------------------------------------------------------------------------- #
# render orchestration — build_clip_specs (pure) + run_render (glue)
# --------------------------------------------------------------------------- #


def test_build_clip_specs_caps_duration_shifts_captions_and_names_files() -> None:
    from src.intelligence.ranking import Moment
    from src.render import build_clip_specs
    from src.render.auto_reframe import ReframePlan
    from src.render.backend import CaptionStyle, RenderFormat

    moments = [
        Moment(
            rank=1,
            start_sec=100.0,
            end_sec=175.0,  # 75s span -> must cap at 60
            duration_sec=75.0,
            composite_score=0.7,
            scene_snapped=False,
            signal_contributions={},
        )
    ]
    words = [("hello", 100.0, 100.4), ("world", 100.4, 101.0), ("late", 170.0, 170.5)]
    specs = build_clip_specs(
        moments,
        source=Path("vid.mp4"),
        words=words,
        fmt=RenderFormat(width=1080, height=1920, fps=30, bitrate="6M"),
        caption_style=CaptionStyle(),
        resolve_reframe=lambda _m: ReframePlan(method="center_crop", foreground_scale=1.0),
        max_words_per_line=4,
        output_dir=Path("/out"),
        source_name="vid",
        date="20260527",
        watermark=None,
        max_duration_sec=60.0,
    )

    assert len(specs) == 1
    spec = specs[0]
    assert spec.start_sec == 100.0
    assert spec.end_sec == 160.0  # capped at start + 60
    assert spec.output_path == Path("/out/20260527_vid_clip_01.mp4")
    # 'late' (170s) is past the capped window -> excluded; hello+world grouped, clip-relative
    assert len(spec.captions) == 1
    assert spec.captions[0].text == "hello world"
    assert spec.captions[0].start_sec == 0.0  # 100 - 100
    assert abs(spec.captions[0].end_sec - 1.0) < 1e-9  # 101 - 100


class _RecordingBackend:
    def __init__(self) -> None:
        self.specs: list[object] = []

    def render_clip(self, spec: object) -> Path:
        self.specs.append(spec)
        return spec.output_path  # type: ignore[attr-defined]


def test_run_render_renders_each_moment_via_injected_backend(tmp_path: Path) -> None:
    from src.config import load_config, load_mode_config
    from src.ingestion.workspace import create_workspace
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )
    from src.render import run_render
    from src.utils.fileio import write_json

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_r")
    write_json(
        ws.transcript_path,
        {
            "language": "en",
            "text": "hi there",
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "hi there",
                    "words": [
                        {"word": "hi", "start": 0.2, "end": 0.4, "score": 0.9},
                        {"word": "there", "start": 0.5, "end": 0.9, "score": 0.9},
                    ],
                }
            ],
            "word_count": 2,
            "aligned": True,
        },
    )
    mr = MomentsResult(
        run_id="run_r",
        mode="shortform",
        source=str(source),
        generated_at="t",
        source_duration_sec=120.0,
        window_sec=60.0,
        composite_method="weighted_sum",
        score_threshold=0.55,
        diversity=DiversityReport(min_gap_sec=30.0, max_clips_per_minute=2.0),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=0.0,
            end_sec=60.0,
            composite_score=0.7,
            signal_contributions={},
            signal_values={},
        ),
        moments=[
            Moment(
                rank=1,
                start_sec=0.0,
                end_sec=60.0,
                duration_sec=60.0,
                composite_score=0.7,
                scene_snapped=False,
                signal_contributions={},
            )
        ],
        moment_count=1,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )

    backend = _RecordingBackend()
    outputs = run_render(
        source, ws, load_config(), load_mode_config("shortform"), mr, backend=backend
    )

    # shortform default is dual_output -> one auto/ clip + one zoom_crop/ clip per moment
    assert len(outputs) == 2
    assert len(backend.specs) == 2
    spec = next(s for s in backend.specs if s.output_path.parent.name == "auto")
    assert spec.fmt.width == 1080 and spec.fmt.height == 1920
    assert spec.reframe_method == "blur_pad"  # auto decision (no subjects -> blur_pad fallback)
    assert spec.captions  # captions built from transcript words
    # composition caption zoning wired through from shortform.yaml (y 0.80, 900px safe)
    assert spec.caption_style.margin_v == 384
    assert spec.caption_style.margin_l == 90 and spec.caption_style.margin_r == 90
    assert all(o.name.endswith("_clip_01.mp4") for o in outputs)


def test_run_render_writes_reframe_decision_into_moments_json(tmp_path: Path) -> None:
    import json

    from src.config import load_config, load_mode_config
    from src.ingestion.workspace import create_workspace
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )
    from src.render import run_render
    from src.utils.fileio import write_json

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_rf")
    # subjects.json: 3 people across the moment window -> 3-5 bucket -> blur_pad bucket_3_5_fs (1.0)
    write_json(
        ws.analysis_dir / "subjects.json",
        {
            "duration_sec": 60.0,
            "sample_rate_hz": 1.0,
            "frame_width": 1920,
            "frame_height": 1080,
            "frames": [
                {
                    "time_sec": float(t),
                    "subject_count": 3,
                    "spread_ratio": 0.8,
                    "is_solo": False,
                    "group_bbox": [200.0, 100.0, 1700.0, 1000.0],
                    "boxes": [],
                }
                for t in (10, 11, 12)
            ],
        },
    )
    mr = MomentsResult(
        run_id="run_rf",
        mode="shortform",
        source=str(source),
        generated_at="t",
        source_duration_sec=120.0,
        window_sec=60.0,
        composite_method="weighted_sum",
        score_threshold=0.55,
        diversity=DiversityReport(min_gap_sec=30.0, max_clips_per_minute=2.0),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=0.0,
            end_sec=60.0,
            composite_score=0.7,
            signal_contributions={},
            signal_values={},
        ),
        moments=[
            Moment(
                rank=1,
                start_sec=0.0,
                end_sec=60.0,
                duration_sec=60.0,
                composite_score=0.7,
                scene_snapped=False,
                signal_contributions={},
            )
        ],
        moment_count=1,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )

    run_render(
        source, ws, load_config(), load_mode_config("shortform"), mr, backend=_RecordingBackend()
    )

    # decision attached to the in-memory moment
    dec = mr.moments[0].reframe
    assert dec is not None
    assert dec.method == "blur_pad"
    assert dec.foreground_scale == 1.0  # 3-5 bucket, tuned value
    assert dec.median_count == 3
    assert abs(dec.max_spread - 0.8) < 1e-9
    # AND persisted into moments.json for tuning visibility
    on_disk = json.loads(ws.moments_path.read_text())
    assert on_disk["moments"][0]["reframe"]["method"] == "blur_pad"
    assert on_disk["moments"][0]["reframe"]["median_count"] == 3


def test_run_render_dual_output_renders_auto_and_zoom_crop(tmp_path: Path) -> None:
    from src.config import load_config, load_mode_config
    from src.ingestion.workspace import create_workspace
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )
    from src.render import run_render

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_dual")
    mr = MomentsResult(
        run_id="run_dual",
        mode="shortform",
        source=str(source),
        generated_at="t",
        source_duration_sec=120.0,
        window_sec=60.0,
        composite_method="weighted_sum",
        score_threshold=0.55,
        diversity=DiversityReport(min_gap_sec=30.0, max_clips_per_minute=2.0),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=0.0,
            end_sec=60.0,
            composite_score=0.7,
            signal_contributions={},
            signal_values={},
        ),
        moments=[
            Moment(
                rank=1,
                start_sec=0.0,
                end_sec=60.0,
                duration_sec=60.0,
                composite_score=0.7,
                scene_snapped=False,
                signal_contributions={},
            )
        ],
        moment_count=1,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )

    backend = _RecordingBackend()
    outputs = run_render(
        source, ws, load_config(), load_mode_config("shortform"), mr, backend=backend
    )

    # one auto/ clip + one zoom_crop/ clip per moment, same filename for easy comparison
    assert len(outputs) == 2
    by_dir = {s.output_path.parent.name: s for s in backend.specs}
    assert set(by_dir) == {"auto", "zoom_crop"}
    assert by_dir["auto"].reframe_method == "blur_pad"  # auto decision (no subjects -> fallback)
    assert by_dir["zoom_crop"].reframe_method == "zoom_crop"  # forced parallel framing
    assert by_dir["zoom_crop"].foreground_scale == 1.3  # zoom_crop_decision default
    assert by_dir["auto"].output_path.name == by_dir["zoom_crop"].output_path.name
    # moments.json records the AUTO decision (zoom_crop is a parallel render, not the decision)
    assert mr.moments[0].reframe is not None
    assert mr.moments[0].reframe.method == "blur_pad"


# --------------------------------------------------------------------------- #
# auto_reframe (Phase 4.5 Layer 2) — aggregation, decision buckets, smoothing, crop-path
# --------------------------------------------------------------------------- #


def _fs(time_sec: float, count: int, spread: float, center_x: float = 960.0):
    from src.analysis.subjects import FrameSubjects

    return FrameSubjects(
        time_sec=time_sec,
        subject_count=count,
        spread_ratio=spread,
        is_solo=count == 1,
        group_bbox=[center_x - 100, 100.0, center_x + 100, 1000.0] if count else None,
        boxes=[],
    )


def test_frames_in_window_filters_by_time() -> None:
    from src.render.auto_reframe import frames_in_window

    frames = [_fs(t, 1, 0.2) for t in (0.0, 5.0, 10.0, 15.0, 20.0)]
    inside = frames_in_window(frames, 4.0, 16.0)
    assert [f.time_sec for f in inside] == [5.0, 10.0, 15.0]


def test_aggregate_window_median_count_and_max_spread() -> None:
    from src.render.auto_reframe import aggregate_window

    frames = [_fs(0, 1, 0.2), _fs(1, 2, 0.5), _fs(2, 2, 0.4), _fs(3, 3, 0.6)]
    prof = aggregate_window(frames)
    assert prof.median_count == 2  # median of [1,2,2,3]
    assert prof.max_spread == 0.6  # conservative guard
    assert prof.frame_count == 4


def test_aggregate_window_empty() -> None:
    from src.render.auto_reframe import aggregate_window

    prof = aggregate_window([])
    assert prof.frame_count == 0
    assert prof.median_count == 0
    assert prof.max_spread == 0.0


def _decision_cfg():
    from src.config import load_config

    return load_config().reframe_decision


def test_decide_solo_narrow_is_track() -> None:
    from src.render.auto_reframe import decide_reframe

    choice = decide_reframe(1, 0.30, _decision_cfg())
    assert choice.method == "track"


def test_decide_solo_wide_is_blur_pad_full() -> None:
    from src.render.auto_reframe import decide_reframe

    choice = decide_reframe(1, 0.70, _decision_cfg())  # box too wide to track
    assert choice.method == "blur_pad"
    assert choice.foreground_scale == 1.0


def test_decide_two_people_blur_pad_1_2() -> None:
    from src.render.auto_reframe import decide_reframe

    choice = decide_reframe(2, 0.5, _decision_cfg())
    assert choice.method == "blur_pad"
    assert choice.foreground_scale == 1.2  # tuned down from 1.3 (edge crop on real footage)


def test_decide_small_group_blur_pad_1_0() -> None:
    from src.render.auto_reframe import decide_reframe

    for n in (3, 4, 5):
        choice = decide_reframe(n, 0.8, _decision_cfg())
        assert choice.method == "blur_pad"
        assert choice.foreground_scale == 1.0  # tuned down from 1.1 to match known-good 1.0 render


def test_decide_large_group_blur_pad_1_0() -> None:
    from src.render.auto_reframe import decide_reframe

    choice = decide_reframe(7, 0.95, _decision_cfg())
    assert choice.method == "blur_pad"
    assert choice.foreground_scale == 1.0


def test_decide_no_subject_fallback_blur_pad_1_0() -> None:
    from src.render.auto_reframe import decide_reframe

    choice = decide_reframe(0, 0.0, _decision_cfg())
    assert choice.method == "blur_pad"
    assert choice.foreground_scale == 1.0


def test_one_euro_constant_input_is_stable() -> None:
    from src.render.auto_reframe import one_euro_smooth

    out = one_euro_smooth([0.0, 1.0, 2.0, 3.0], [5.0, 5.0, 5.0, 5.0])
    assert all(math.isclose(v, 5.0, abs_tol=1e-9) for v in out)


def test_one_euro_reduces_jitter() -> None:
    from src.render.auto_reframe import one_euro_smooth

    noisy = [10.0, 12.0, 8.0, 11.0, 9.0, 12.0, 8.0]
    times = [float(i) for i in range(len(noisy))]
    out = one_euro_smooth(times, noisy)
    assert (max(out) - min(out)) < (max(noisy) - min(noisy))  # glides, not jitter


def test_one_euro_follows_a_step() -> None:
    from src.render.auto_reframe import one_euro_smooth

    vals = [0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0]
    times = [float(i) for i in range(len(vals))]
    out = one_euro_smooth(times, vals)
    assert out[-1] > out[2]  # responds to the move
    assert out[-1] > 5.0  # converges toward the new value (no permanent lag)


def test_track_crop_path_clamps_and_centers() -> None:
    from src.render.auto_reframe import track_crop_path

    # constant centre -> crop x = centre - crop_w/2, clamped to [0, frame_w - crop_w]
    centred = track_crop_path([(0.0, 960.0), (1.0, 960.0)], crop_w=608, frame_w=1920)
    assert all(math.isclose(x, 656.0, abs_tol=1.0) for _, x in centred)  # 960 - 304

    left = track_crop_path([(0.0, 100.0), (1.0, 100.0)], crop_w=608, frame_w=1920)
    assert all(x == 0.0 for _, x in left)  # clamped at left edge

    right = track_crop_path([(0.0, 1850.0), (1.0, 1850.0)], crop_w=608, frame_w=1920)
    assert all(math.isclose(x, 1312.0, abs_tol=1.0) for _, x in right)  # 1920 - 608


def test_crop_x_expr_single_point_is_constant() -> None:
    from src.render.auto_reframe import crop_x_expr

    assert crop_x_expr([(0.0, 656.0)]) == "656.000"


def test_crop_x_expr_two_points_piecewise_linear() -> None:
    from src.render.auto_reframe import crop_x_expr

    expr = crop_x_expr([(0.0, 100.0), (2.0, 300.0)])
    assert expr == ("if(lt(t,2.000),100.000+(300.000-100.000)*(t-0.000)/(2.000-0.000),300.000)")


# --------------------------------------------------------------------------- #
# zoom_crop subject-aware centering (cover-scale-aware crop-x)
# --------------------------------------------------------------------------- #
# zoom_crop cover-scales the source by `foreground_scale` (force_original_aspect_ratio=
# increase) THEN crops target WxH. So a subject's source-x must be mapped INTO scaled
# space before we can centre the crop on it:
#     s           = max(zoom_w/source_w, zoom_h/source_h)        # cover-scale factor
#     subj_scaled = subject_x_source * s
#     crop_x      = clamp(subj_scaled - target_w/2, 0, source_w*s - target_w)
# Unclamped, the subject lands exactly at target_w/2 (output centre).

_TOL = 2.0  # rounding crop_x to an int pixel offset costs <=0.5px; 2.0 is a safe bound


def _cover_scale(source_w: int, source_h: int, target_w: int, target_h: int, fs: float) -> float:
    from src.render.social_formats import zoom_crop_box

    zoom_w, zoom_h = zoom_crop_box(target_w, target_h, fs)
    return max(zoom_w / source_w, zoom_h / source_h)


def test_zoom_crop_x_centers_subject_at_frame_middle() -> None:
    from src.render.social_formats import compute_zoom_crop_x

    crop_x = compute_zoom_crop_x(
        subject_x_source=960,
        source_w=1920,
        source_h=1080,
        target_w=1080,
        target_h=1920,
        foreground_scale=1.3,
    )
    s = _cover_scale(1920, 1080, 1080, 1920, 1.3)
    subject_pos_in_crop = 960 * s - crop_x
    assert abs(subject_pos_in_crop - 1080 / 2) < _TOL
    assert abs(crop_x - (1920 * s - 1080) / 2) < _TOL  # centred subject ~ old geometric crop


def test_zoom_crop_x_centers_subject_left_quarter() -> None:
    from src.render.social_formats import compute_zoom_crop_x

    crop_x = compute_zoom_crop_x(
        subject_x_source=480,
        source_w=1920,
        source_h=1080,
        target_w=1080,
        target_h=1920,
        foreground_scale=1.3,
    )
    s = _cover_scale(1920, 1080, 1080, 1920, 1.3)
    subject_pos_in_crop = 480 * s - crop_x
    assert crop_x > 0  # no clamp needed
    assert abs(subject_pos_in_crop - 1080 / 2) < _TOL


def test_zoom_crop_x_centers_subject_right_quarter() -> None:
    from src.render.social_formats import compute_zoom_crop_x

    crop_x = compute_zoom_crop_x(
        subject_x_source=1440,
        source_w=1920,
        source_h=1080,
        target_w=1080,
        target_h=1920,
        foreground_scale=1.3,
    )
    s = _cover_scale(1920, 1080, 1080, 1920, 1.3)
    subject_pos_in_crop = 1440 * s - crop_x
    assert crop_x < 1920 * s - 1080  # below the max -> no clamp
    assert abs(subject_pos_in_crop - 1080 / 2) < _TOL


def test_zoom_crop_x_far_left_clamps_to_zero_subject_visible() -> None:
    from src.render.social_formats import compute_zoom_crop_x

    crop_x = compute_zoom_crop_x(
        subject_x_source=50,
        source_w=1920,
        source_h=1080,
        target_w=1080,
        target_h=1920,
        foreground_scale=1.3,
    )
    s = _cover_scale(1920, 1080, 1080, 1920, 1.3)
    subject_pos_in_crop = 50 * s - crop_x
    assert crop_x == 0  # clamped to the left edge
    assert 0 <= subject_pos_in_crop < 1080  # still inside the output frame
    assert subject_pos_in_crop < 1080 / 2  # offset left of centre, expected at the edge


def test_zoom_crop_x_far_right_clamps_to_max_subject_visible() -> None:
    from src.render.social_formats import compute_zoom_crop_x

    crop_x = compute_zoom_crop_x(
        subject_x_source=1870,
        source_w=1920,
        source_h=1080,
        target_w=1080,
        target_h=1920,
        foreground_scale=1.3,
    )
    s = _cover_scale(1920, 1080, 1080, 1920, 1.3)
    scaled_w = 1920 * s
    subject_pos_in_crop = 1870 * s - crop_x
    assert crop_x == round(scaled_w - 1080)  # clamped to the right-most valid crop
    assert 0 <= subject_pos_in_crop < 1080  # still inside the output frame
    assert subject_pos_in_crop > 1080 / 2  # offset right of centre, expected at the edge


def test_zoom_crop_no_detection_falls_back_to_geometric_center() -> None:
    from src.render import zoom_crop_x_for_window
    from src.render.social_formats import reframe_filtergraph

    assert (
        zoom_crop_x_for_window(
            [],
            frame_width=1920,
            frame_height=1080,
            target_w=1080,
            target_h=1920,
            foreground_scale=1.3,
        )
        is None
    )
    # a None crop-x reproduces the EXACT current geometric-centre filtergraph string
    assert reframe_filtergraph("zoom_crop", 1080, 1920, foreground_scale=1.3, zoom_crop_x=None) == (
        "[0:v]scale=1404:2496:force_original_aspect_ratio=increase,crop=1080:1920[vout]"
    )


def test_zoom_crop_multi_person_centers_on_group_bbox_median() -> None:
    from src.render import zoom_crop_x_for_window
    from src.render.social_formats import compute_zoom_crop_x

    # group-bbox centres across 3 frames: 800, 1000, 1200 -> median 1000
    frames = [_fs(0.0, 2, 0.2, 800.0), _fs(1.0, 3, 0.3, 1000.0), _fs(2.0, 2, 0.2, 1200.0)]
    crop_x = zoom_crop_x_for_window(
        frames,
        frame_width=1920,
        frame_height=1080,
        target_w=1080,
        target_h=1920,
        foreground_scale=1.3,
    )
    expected = compute_zoom_crop_x(
        subject_x_source=1000,
        source_w=1920,
        source_h=1080,
        target_w=1080,
        target_h=1920,
        foreground_scale=1.3,
    )
    assert crop_x == expected  # centred on the median group centre; multiple people, no crash


def test_current_geometric_center_crops_off_a_right_side_subject() -> None:
    """RED on f437dd8 (AssertionError), GREEN after the fix — numeric proof, not just
    'symbol missing': today's geometric-centre zoom_crop pushes a 75%-right subject
    off-frame, and the subject-aware crop brings it back to centre."""
    from src.render.social_formats import reframe_filtergraph

    # today's zoom_crop emits a bare crop with NO explicit x -> ffmpeg uses the geometric
    # centre (scaled_w - target_w)/2 at runtime. Confirm there is no x to honour the subject.
    current = reframe_filtergraph("zoom_crop", 1080, 1920, foreground_scale=1.3)
    assert ":x=" not in current

    s = _cover_scale(1920, 1080, 1080, 1920, 1.3)
    try:
        from src.render.social_formats import compute_zoom_crop_x

        crop_x: float = compute_zoom_crop_x(
            subject_x_source=1440,
            source_w=1920,
            source_h=1080,
            target_w=1080,
            target_h=1920,
            foreground_scale=1.3,
        )
    except ImportError:
        crop_x = (1920 * s - 1080) / 2  # what ffmpeg uses today (geometric centre, ~1679)

    subject_pos_in_crop = 1440 * s - crop_x
    assert 0 <= subject_pos_in_crop <= 1080, (
        f"75%-right subject lands at {subject_pos_in_crop:.0f}px in a 1080-wide output "
        f"under crop_x={crop_x:.0f} (geometric centre) — off-frame, the bug we are fixing"
    )
    assert abs(subject_pos_in_crop - 1080 / 2) < _TOL


# --------------------------------------------------------------------------- #
# Phase 6 — parallel render (written before implementation)
# Byte-equivalence guarantee at the orchestration level: same specs in -> same
# files out, returned list stays spec-ordered regardless of completion order.
# --------------------------------------------------------------------------- #


def _moments_result(source, n_moments: int):
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )

    moments = [
        Moment(
            rank=i + 1,
            start_sec=float(i * 60),
            end_sec=float(i * 60 + 60),
            duration_sec=60.0,
            composite_score=0.7,
            scene_snapped=False,
            signal_contributions={},
        )
        for i in range(n_moments)
    ]
    return MomentsResult(
        run_id="run_p",
        mode="shortform",
        source=str(source),
        generated_at="t",
        source_duration_sec=float(n_moments * 60 + 60),
        window_sec=60.0,
        composite_method="weighted_sum",
        score_threshold=0.55,
        diversity=DiversityReport(min_gap_sec=30.0, max_clips_per_minute=2.0),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=0.0,
            end_sec=60.0,
            composite_score=0.7,
            signal_contributions={},
            signal_values={},
        ),
        moments=moments,
        moment_count=n_moments,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )


def _seed_render_workspace(tmp_path, run_id: str, n_moments: int):
    from src.ingestion.workspace import create_workspace
    from src.utils.fileio import write_json

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id=run_id)
    write_json(
        ws.transcript_path,
        {
            "language": "en",
            "text": "hi there",
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "hi there",
                    "words": [{"word": "hi", "start": 0.2, "end": 0.4, "score": 0.9}],
                }
            ],
            "word_count": 1,
            "aligned": True,
        },
    )
    return source, ws, _moments_result(source, n_moments)


def _rank_of(path) -> int:
    import re

    m = re.search(r"clip_(\d+)", path.name)
    return int(m.group(1)) if m else -1


def test_parallel_render_dispatch_equivalence(tmp_path) -> None:
    import threading
    import time as _t

    from src.config import load_config, load_mode_config
    from src.render import run_render

    source, ws, mr = _seed_render_workspace(tmp_path, "run_eq", n_moments=2)

    completed: list = []
    lock = threading.Lock()

    class _OrderingBackend:
        def render_clip(self, spec):
            # rank-1 sleeps longest so it finishes LAST -> completion order != spec order
            _t.sleep(0.06 if _rank_of(spec.output_path) == 1 else 0.0)
            with lock:
                completed.append(spec.output_path)
            return spec.output_path

    outputs = run_render(
        source, ws, load_config(), load_mode_config("shortform"), mr, backend=_OrderingBackend()
    )
    auto = [p for p in outputs if p.parent.name == "auto"]
    # returned list is spec-ordered (rank 1 then 2) even though it completed reversed
    assert [_rank_of(p) for p in auto] == [1, 2]
    assert _rank_of(completed[0]) == 2  # confirms out-of-order completion actually happened


def test_render_lane_cap_respected(tmp_path) -> None:
    import threading
    import time as _t

    from src.config import load_config, load_mode_config
    from src.render import run_render

    source, ws, mr = _seed_render_workspace(tmp_path, "run_cap", n_moments=4)

    current = 0
    observed_max = 0
    lock = threading.Lock()

    class _ConcurrencyBackend:
        def render_clip(self, spec):
            nonlocal current, observed_max
            with lock:
                current += 1
                observed_max = max(observed_max, current)
            _t.sleep(0.03)
            with lock:
                current -= 1
            return spec.output_path

    run_render(
        source, ws, load_config(), load_mode_config("shortform"), mr, backend=_ConcurrencyBackend()
    )
    assert observed_max == 2  # config max_render_workers


def test_dual_output_unchanged_under_parallel(tmp_path) -> None:
    """CRITICAL regression guard: dual auto/ + zoom_crop/ output survives parallel render."""
    from src.config import load_config, load_mode_config
    from src.render import run_render

    source, ws, mr = _seed_render_workspace(tmp_path, "run_dual", n_moments=2)

    class _Recorder:
        def __init__(self) -> None:
            self.specs: list = []

        def render_clip(self, spec):
            self.specs.append(spec)
            return spec.output_path

    backend = _Recorder()
    outputs = run_render(
        source, ws, load_config(), load_mode_config("shortform"), mr, backend=backend
    )
    auto = sorted(p.name for p in outputs if p.parent.name == "auto")
    zoom = sorted(p.name for p in outputs if p.parent.name == "zoom_crop")
    assert len(auto) == 2 and len(zoom) == 2
    # identical filenames across the two folders (the Phase 4.5 A/B invariant)
    assert auto == zoom


# --------------------------------------------------------------------------- #
# Phase 7 — standard_longform render path (separate, isolated longform module).
# 16:9 native cut, NO reframe / NO captions / NO watermark (Descript handles
# polish). The frozen shortform path above is untouched.
# --------------------------------------------------------------------------- #


def test_build_longform_command_scales_to_1080p() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, build_longform_command

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=120.0,
        end_sec=420.0,  # 5-min cut
        fmt=RenderFormat(width=1920, height=1080, fps=30, video_codec="h264", bitrate="8M"),
        output_path=Path("out.mp4"),
    )
    args = build_longform_command(spec, encoder="h264_videotoolbox")

    assert args[0] == "-y"
    assert args[args.index("-ss") + 1] == "120.000"  # fast input seek to window start
    assert args[args.index("-i") + 1] == "in.mp4"
    assert args[args.index("-t") + 1] == "300.000"  # duration = 420 - 120
    assert args[args.index("-vf") + 1] == "scale=1920:1080"  # plain scale, 16:9, no reframe
    assert args[args.index("-c:v") + 1] == "h264_videotoolbox"
    assert args[args.index("-b:v") + 1] == "8M"
    assert args[args.index("-c:a") + 1] == "aac"
    assert "+faststart" in args
    assert args[-1] == "out.mp4"


def test_build_longform_command_has_no_caption_filter() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, build_longform_command

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=Path("out.mp4"),
    )
    args = build_longform_command(spec, encoder="libx264")
    assert "subtitles=" not in " ".join(args)  # Descript burns captions, not the engine
    assert "-filter_complex" not in args  # plain -vf scale, no caption/overlay chain


def test_build_longform_command_has_no_watermark_or_reframe() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, build_longform_command

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=Path("out.mp4"),
    )
    joined = " ".join(build_longform_command(spec, encoder="libx264"))
    assert "overlay" not in joined  # no watermark overlay
    assert "gblur" not in joined and "split" not in joined  # no blur_pad reframe compositor
    assert "crop=" not in joined  # 16:9 native -> no reframe crop


def _longform_moments(source, *, n: int = 1, fallback: bool = False):
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )

    moments = (
        [
            Moment(
                rank=1,
                start_sec=60.0,
                end_sec=360.0,
                duration_sec=300.0,
                composite_score=0.6,
                scene_snapped=False,
                signal_contributions={},
            )
        ]
        if n
        else []
    )
    fb = (
        [
            Moment(
                rank=1,
                start_sec=10.0,
                end_sec=310.0,
                duration_sec=300.0,
                composite_score=0.3,
                scene_snapped=False,
                signal_contributions={},
            )
        ]
        if fallback
        else []
    )
    return MomentsResult(
        run_id="run_lf",
        mode="standard_longform",
        source=str(source),
        generated_at="t",
        source_duration_sec=900.0,
        window_sec=300.0,
        composite_method="weighted_sum",
        score_threshold=0.4,
        diversity=DiversityReport(min_gap_sec=15.0, max_clips_per_minute=None),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=10.0,
            end_sec=310.0,
            composite_score=0.3,
            signal_contributions={},
            signal_values={},
        ),
        moments=moments,
        moment_count=len(moments),
        used_fallback=fallback,
        below_threshold_best_candidates=fb,
    )


def test_select_best_window_prefers_threshold_moment(tmp_path: Path) -> None:
    from src.render.longform import select_best_window

    best = select_best_window(_longform_moments(tmp_path / "v.mp4", n=1))
    assert best.start_sec == 60.0 and best.end_sec == 360.0


def test_select_best_window_falls_back_to_below_threshold(tmp_path: Path) -> None:
    from src.render.longform import select_best_window

    best = select_best_window(_longform_moments(tmp_path / "v.mp4", n=0, fallback=True))
    assert best.start_sec == 10.0  # the below-threshold candidate when no moment cleared


def test_longform_backend_encoder_fallback(monkeypatch, tmp_path: Path) -> None:
    import src.render.longform as lf
    from src.render.backend import RenderFormat
    from src.render.longform import LongformBackend, LongformClipSpec
    from src.utils.ffmpeg import FFmpegError

    calls: list[str] = []

    def fake_run_ffmpeg(args, *a, **k):
        encoder = args[args.index("-c:v") + 1]
        calls.append(encoder)
        if encoder == "h264_videotoolbox":
            raise FFmpegError("videotoolbox boom")
        return None

    monkeypatch.setattr(lf, "run_ffmpeg", fake_run_ffmpeg)
    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=tmp_path / "out.mp4",
    )
    out = LongformBackend().render_clip(spec)
    assert out == tmp_path / "out.mp4"
    assert calls == ["h264_videotoolbox", "libx264"]  # HW first, SW fallback (SPEC §9)


class _RecordingLongformBackend:
    def __init__(self) -> None:
        self.specs: list = []

    def render_clip(self, spec):
        self.specs.append(spec)
        return spec.output_path


def test_run_longform_render_renders_single_16x9_window(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config, load_mode_config
    from src.ingestion.workspace import create_workspace
    from src.render.longform import run_longform_render

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_lf")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])  # no real ffmpeg frames

    backend = _RecordingLongformBackend()
    outputs = run_longform_render(
        source,
        ws,
        load_config(),
        load_mode_config("standard_longform"),
        _longform_moments(source, n=1),
        backend=backend,
    )

    assert len(outputs) == 1  # ONE 16:9 video per source (multi-window stitch = Phase 7b)
    assert len(backend.specs) == 1
    spec = backend.specs[0]
    assert spec.start_sec == 60.0 and spec.end_sec == 360.0  # the selected best window
    assert spec.fmt.width == 1920 and spec.fmt.height == 1080  # 16:9 native, no reframe
    assert outputs[0].name.endswith("_standard_longform.mp4")
    assert outputs[0].parent.name == "standard" and outputs[0].parent.parent.name == "longform"


def test_run_longform_render_writes_thumbnail_fields_into_moments(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    import src.render.longform as lf
    from src.config import load_config, load_mode_config
    from src.ingestion.workspace import create_workspace
    from src.render.longform import run_longform_render

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_lf2")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [Path("/t/thumb_01.jpg")])

    run_longform_render(
        source,
        ws,
        load_config(),
        load_mode_config("standard_longform"),
        _longform_moments(source, n=1),
        backend=_RecordingLongformBackend(),
    )

    on_disk = json.loads(ws.moments_path.read_text())
    assert on_disk["thumbnail_candidates"] == ["/t/thumb_01.jpg"]
    assert "thumbnail_text_suggestions" in on_disk
    # no transcript.json in this workspace -> the distinct "unavailable" status
    assert on_disk["thumbnail_text_status"] == "transcript_unavailable"


# --------------------------------------------------------------------------- #
# Phase 7 — thumbnail frame selection (pure) + extraction (integration)
# --------------------------------------------------------------------------- #


def test_thumbnail_composite_sums_three_metrics() -> None:
    from src.render.thumbnails import thumbnail_composite

    score = thumbnail_composite(
        {"composition_score": 0.5, "lighting_quality": 0.3, "sharpness": 0.2, "color_harmony": 0.9}
    )
    assert abs(score - 1.0) < 1e-9  # composition + lighting + sharpness; color_harmony ignored


def test_select_thumbnail_frames_within_window() -> None:
    from src.render.thumbnails import select_thumbnail_frames

    times = [0.0, 50.0, 100.0, 150.0, 200.0, 350.0]
    metrics = {
        "composition_score": [0.1, 0.9, 0.8, 0.7, 0.6, 0.95],
        "lighting_quality": [0.1, 0.9, 0.8, 0.7, 0.6, 0.95],
        "sharpness": [0.1, 0.9, 0.8, 0.7, 0.6, 0.95],
    }
    picked = select_thumbnail_frames(
        times, metrics, start=60.0, end=300.0, count=3, min_gap_sec=10.0
    )
    assert all(60.0 <= t <= 300.0 for t in picked)  # 0.0 and 350.0 are outside the cut window
    assert 0.0 not in picked and 350.0 not in picked


def test_select_thumbnail_frames_ranks_by_composite() -> None:
    from src.render.thumbnails import select_thumbnail_frames

    times = [60.0, 120.0, 180.0, 240.0]
    metrics = {
        "composition_score": [0.1, 0.9, 0.2, 0.8],
        "lighting_quality": [0.1, 0.9, 0.2, 0.8],
        "sharpness": [0.1, 0.9, 0.2, 0.8],
    }
    picked = select_thumbnail_frames(
        times, metrics, start=0.0, end=300.0, count=2, min_gap_sec=10.0
    )
    assert set(picked) == {120.0, 240.0}  # the two highest-composite frames, not first-N


def test_select_thumbnail_frames_respects_min_gap() -> None:
    from src.render.thumbnails import select_thumbnail_frames

    times = [60.0, 61.0, 62.0, 120.0]
    metrics = {
        "composition_score": [0.9, 0.88, 0.86, 0.5],
        "lighting_quality": [0.9, 0.88, 0.86, 0.5],
        "sharpness": [0.9, 0.88, 0.86, 0.5],
    }
    picked = sorted(
        select_thumbnail_frames(times, metrics, start=0.0, end=300.0, count=3, min_gap_sec=30.0)
    )
    assert picked == [60.0, 120.0]  # 61/62 within 30s of 60 -> skipped; no 3 adjacent frames


def test_select_thumbnail_frames_count_cap() -> None:
    from src.render.thumbnails import select_thumbnail_frames

    times = [float(t) for t in range(0, 300, 20)]
    metrics = {
        m: [0.5] * len(times) for m in ("composition_score", "lighting_quality", "sharpness")
    }
    picked = select_thumbnail_frames(
        times, metrics, start=0.0, end=300.0, count=3, min_gap_sec=10.0
    )
    assert len(picked) == 3  # capped at the requested count
    assert all(0.0 <= t <= 300.0 for t in picked)


def test_select_thumbnail_frames_no_data_falls_back_to_middle() -> None:
    from src.render.thumbnails import select_thumbnail_frames

    picked = select_thumbnail_frames([], {}, start=60.0, end=360.0, count=3, min_gap_sec=10.0)
    assert picked == [210.0]  # deterministic middle of the window (NOT random, so it's testable)


@pytest.mark.integration
def test_extract_thumbnail_frame_writes_jpeg(sample_video: Path, tmp_path: Path) -> None:
    from src.render.thumbnails import extract_thumbnail_frame

    out = tmp_path / "thumb.jpg"
    result = extract_thumbnail_frame(sample_video, 1.5, out, width=1920, height=1080)
    assert result == out
    assert out.exists() and out.stat().st_size > 0
    assert out.read_bytes()[:2] == b"\xff\xd8"  # JPEG SOI marker


@pytest.mark.integration
def test_longform_backend_real_encode_is_16x9_with_audio(
    sample_video: Path, tmp_path: Path
) -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformBackend, LongformClipSpec

    out = tmp_path / "cut.mp4"
    spec = LongformClipSpec(
        source=sample_video,
        start_sec=0.0,
        end_sec=3.0,  # short cut keeps the test fast; geometry/audio is what matters
        fmt=RenderFormat(width=1920, height=1080, fps=30, bitrate="8M"),
        output_path=out,
    )
    result = LongformBackend().render_clip(spec)

    assert result == out and out.exists() and out.stat().st_size > 0
    probe = ffprobe_json(out)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    assert int(video["width"]) == 1920 and int(video["height"]) == 1080  # 16:9 native
    assert audio is not None  # source audio preserved for Descript
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.7


# --------------------------------------------------------------------------- #
# Phase 8 — cinematic_longform: single-stage lut3d grade on the longform path.
# The LUT goes into the existing single `-vf` chain BEFORE scale (grade at source
# resolution, then downsample). standard_longform stays ungraded (no regression).
# build_longform_command emits only command strings here; the real Sony LUT is
# owner IP (gitignored) so config/render tests point at tests/fixtures/identity.cube.
# --------------------------------------------------------------------------- #

_IDENTITY_LUT = Path("tests/fixtures/identity.cube")  # relative to PROJECT_ROOT


def test_lutgrade_accepts_path_and_defaults_tetrahedral() -> None:
    from src.render.longform import LutGrade

    g = LutGrade(path=Path("look.cube"))
    assert g.path == Path("look.cube")
    assert g.interp == "tetrahedral"  # sensible ffmpeg default


def test_lutgrade_accepts_str_path_coerced_to_path() -> None:
    from src.render.longform import LutGrade

    g = LutGrade(path="look.cube")
    assert g.path == Path(
        "look.cube"
    )  # Pydantic coerces str -> Path (like LongformClipSpec.source)


def test_lutgrade_rejects_unknown_interp() -> None:
    from pydantic import ValidationError

    from src.render.longform import LutGrade

    with pytest.raises(ValidationError):
        LutGrade(path=Path("look.cube"), interp="bogus")


def test_longform_clipspec_lut_defaults_none() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=Path("out.mp4"),
    )
    assert spec.lut is None  # ungraded by default (standard_longform path)


def test_longform_clipspec_accepts_lutgrade() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, LutGrade

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=Path("out.mp4"),
        lut=LutGrade(path=Path("look.cube")),
    )
    assert spec.lut is not None
    assert spec.model_dump()["lut"]["path"] == Path(
        "look.cube"
    )  # round-trips through serialization


def test_build_longform_command_without_lut_matches_phase7() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, build_longform_command

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=Path("out.mp4"),
    )
    args = build_longform_command(spec, encoder="libx264")
    assert args[args.index("-vf") + 1] == "scale=1920:1080"  # identical to Phase 7, plain scale
    assert "lut3d" not in " ".join(args)  # no grade when lut is None


def test_build_longform_command_with_lut_prepends_lut3d() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, LutGrade, build_longform_command

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=Path("out.mp4"),
        lut=LutGrade(path=Path("look.cube"), interp="tetrahedral"),
    )
    args = build_longform_command(spec, encoder="libx264")
    assert args[args.index("-vf") + 1] == "lut3d=look.cube:interp=tetrahedral,scale=1920:1080"


def test_build_longform_command_lut_before_scale() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, LutGrade, build_longform_command

    spec = LongformClipSpec(
        source=Path("in.mp4"),
        start_sec=0.0,
        end_sec=300.0,
        fmt=RenderFormat(width=1920, height=1080, fps=30),
        output_path=Path("out.mp4"),
        lut=LutGrade(path=Path("look.cube")),
    )
    vf = build_longform_command(spec, encoder="libx264")[
        build_longform_command(spec, encoder="libx264").index("-vf") + 1
    ]
    assert vf.index("lut3d") < vf.index("scale")  # grade at source res, THEN downsample


def test_build_longform_command_interp_variants() -> None:
    from src.render.backend import RenderFormat
    from src.render.longform import LongformClipSpec, LutGrade, build_longform_command

    for interp in ("trilinear", "nearest"):
        spec = LongformClipSpec(
            source=Path("in.mp4"),
            start_sec=0.0,
            end_sec=300.0,
            fmt=RenderFormat(width=1920, height=1080, fps=30),
            output_path=Path("out.mp4"),
            lut=LutGrade(path=Path("look.cube"), interp=interp),
        )
        args = build_longform_command(spec, encoder="libx264")
        assert f"interp={interp}" in args[args.index("-vf") + 1]


def test_resolve_lut_grade_absent_returns_none() -> None:
    from src.render.longform import resolve_lut_grade

    assert resolve_lut_grade({}) is None  # no color_grade block -> ungraded


def test_resolve_lut_grade_disabled_returns_none() -> None:
    from src.render.longform import resolve_lut_grade

    cg = {"enabled": False, "lut_path": str(_IDENTITY_LUT), "interp": "tetrahedral"}
    assert resolve_lut_grade(cg) is None  # explicitly disabled -> ungraded


def test_resolve_lut_grade_enabled_valid_returns_grade() -> None:
    from src.config import PROJECT_ROOT
    from src.render.longform import resolve_lut_grade

    cg = {"enabled": True, "lut_path": str(_IDENTITY_LUT), "interp": "tetrahedral"}
    grade = resolve_lut_grade(cg)
    assert grade is not None
    assert grade.path == (PROJECT_ROOT / _IDENTITY_LUT).resolve()  # resolved against PROJECT_ROOT
    assert grade.interp == "tetrahedral"


def test_resolve_lut_grade_enabled_missing_path_raises() -> None:
    from src.render.longform import resolve_lut_grade

    with pytest.raises(ValueError, match=r"color_grade\.lut_path"):
        resolve_lut_grade({"enabled": True})  # enabled but no lut_path key


def test_resolve_lut_grade_enabled_file_not_found_raises() -> None:
    from src.config import PROJECT_ROOT
    from src.render.longform import resolve_lut_grade

    cg = {"enabled": True, "lut_path": "nope.cube", "interp": "tetrahedral"}
    resolved = str((PROJECT_ROOT / "nope.cube").resolve())
    with pytest.raises(ValueError, match=r"color_grade\.lut_path file not found"):
        try:
            resolve_lut_grade(cg)
        except ValueError as err:
            assert resolved in str(err)  # resolved absolute path is in the message
            raise


def _cinematic_mode_with_lut(lut_path: str = str(_IDENTITY_LUT)):
    """The real cinematic_longform config, but color_grade.lut_path swapped to a
    committed fixture (the production Sony LUT is owner IP / gitignored)."""
    from src.config import ModeConfig, load_mode_config

    dump = load_mode_config("cinematic_longform").model_dump()
    dump["color_grade"] = {"enabled": True, "lut_path": lut_path, "interp": "tetrahedral"}
    return ModeConfig(**dump)


def test_run_longform_render_cinematic_applies_lut(
    tmp_path: Path, sample_video: Path, monkeypatch
) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.ingestion.workspace import create_workspace
    from src.intelligence.ranking import (
        BestWindow,
        DiversityReport,
        Moment,
        MomentsResult,
        WeightsReport,
    )
    from src.render.longform import run_longform_render

    # Phase 10: cinematic now runs technical correction, which samples real frames from
    # the SELECTED WINDOW. Use the real 60s fixture with a window inside it — the shared
    # _longform_moments helper's 60-360s window would clamp to empty on a 60s source.
    source = sample_video
    moment = Moment(
        rank=1,
        start_sec=5.0,
        end_sec=45.0,
        duration_sec=40.0,
        composite_score=0.6,
        scene_snapped=False,
        signal_contributions={},
    )
    moments = MomentsResult(
        run_id="run_cine",
        mode="cinematic_longform",
        source=str(source),
        generated_at="t",
        source_duration_sec=60.0,
        window_sec=40.0,
        composite_method="weighted_sum",
        score_threshold=0.4,
        diversity=DiversityReport(min_gap_sec=15.0, max_clips_per_minute=None),
        weights=WeightsReport(configured={}, effective={}, unavailable_signals=[]),
        best_window=BestWindow(
            start_sec=5.0,
            end_sec=45.0,
            composite_score=0.6,
            signal_contributions={},
            signal_values={},
        ),
        moments=[moment],
        moment_count=1,
        used_fallback=False,
        below_threshold_best_candidates=[],
    )
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_cine")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])

    backend = _RecordingLongformBackend()
    outputs = run_longform_render(
        source,
        ws,
        load_config(),
        _cinematic_mode_with_lut(),
        moments,
        backend=backend,
    )

    spec = backend.specs[0]
    assert spec.lut is not None  # cinematic mode populates the grade
    assert spec.correction != ""  # Phase 10: real auto-correction produced a filter
    assert outputs[0].name.endswith("_cinematic_longform.mp4")
    assert outputs[0].parent.name == "cinematic" and outputs[0].parent.parent.name == "longform"


def test_run_longform_render_standard_has_no_lut(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config, load_mode_config
    from src.ingestion.workspace import create_workspace
    from src.render.longform import run_longform_render

    source = tmp_path / "vid.mp4"
    source.write_bytes(b"x")
    ws = create_workspace(source, tmp_path / "workspace", run_id="run_std")
    monkeypatch.setattr(lf, "extract_thumbnails", lambda *a, **k: [])

    backend = _RecordingLongformBackend()
    run_longform_render(
        source,
        ws,
        load_config(),
        load_mode_config("standard_longform"),
        _longform_moments(source, n=1),
        backend=backend,
    )
    assert backend.specs[0].lut is None  # standard_longform stays ungraded (Descript owns grade)


# --------------------------------------------------------------------------- #
# Standalone `cme grade` — apply a mode's LUT to a FULL file (no selection/cut).
# libx264 CRF master (default native resolution, correction opt-in). Written
# before implementation: build_grade_command / run_grade / GRADE_FULL_FILE_SEC
# do not exist yet, so these fail RED at import.
# --------------------------------------------------------------------------- #


def _grade_cmd(**kw):
    from src.render.backend import RenderFormat
    from src.render.longform import LutGrade, build_grade_command

    source = kw.pop("source", Path("in.mp4"))
    output = kw.pop("output", Path("out.mp4"))
    kw.setdefault("lut", LutGrade(path=Path("look.cube"), interp="tetrahedral"))
    kw.setdefault("fmt", RenderFormat(width=1920, height=1080, fps=30))
    return build_grade_command(source, output, **kw)


def test_build_grade_command_has_lut3d_no_seek_no_cut() -> None:
    args = _grade_cmd()
    assert "lut3d=look.cube:interp=tetrahedral" in args[args.index("-vf") + 1]
    assert "-ss" not in args  # full file: no input seek
    assert "-t" not in args  # full file: no duration cut
    assert "-r" not in args  # preserve native fps
    assert args[args.index("-i") + 1] == "in.mp4"
    assert args[-1] == "out.mp4"


def test_build_grade_command_native_resolution_by_default() -> None:
    args = _grade_cmd()
    vf = args[args.index("-vf") + 1]
    assert vf == "lut3d=look.cube:interp=tetrahedral"  # no scale -> native resolution
    assert "scale=" not in vf


def test_build_grade_command_scale_to_mode_when_requested() -> None:
    args = _grade_cmd(scale=True)
    vf = args[args.index("-vf") + 1]
    assert vf == "lut3d=look.cube:interp=tetrahedral,scale=1920:1080"
    assert vf.index("lut3d") < vf.index("scale")


def test_build_grade_command_uses_crf_18_by_default() -> None:
    args = _grade_cmd()
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-crf") + 1] == "18"
    assert args[args.index("-preset") + 1] == "medium"
    assert "-b:v" not in args  # quality mode, not a fixed delivery bitrate


def test_build_grade_command_quality_override_sets_crf() -> None:
    args = _grade_cmd(crf=16)
    assert args[args.index("-crf") + 1] == "16"


def test_build_grade_command_bitrate_override_replaces_crf() -> None:
    args = _grade_cmd(bitrate="50M")
    assert args[args.index("-b:v") + 1] == "50M"
    assert "-crf" not in args  # explicit bitrate wins over CRF


def test_build_grade_command_audio_copy() -> None:
    args = _grade_cmd()
    assert args[args.index("-c:a") + 1] == "copy"


def test_build_grade_command_correction_prepended_before_lut3d() -> None:
    args = _grade_cmd(correction="eq=brightness=0.1")
    vf = args[args.index("-vf") + 1]
    assert vf == "eq=brightness=0.1,lut3d=look.cube:interp=tetrahedral"
    assert vf.index("eq=") < vf.index("lut3d")


def test_build_grade_command_full_order_correction_lut_scale() -> None:
    args = _grade_cmd(correction="eq=brightness=0.1", scale=True)
    vf = args[args.index("-vf") + 1]
    assert vf.index("eq=") < vf.index("lut3d") < vf.index("scale")


def test_run_grade_resolves_mode_color_grade(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import LutGrade, run_grade

    captured: dict = {}

    def fake_resolve(color_grade):
        captured["color_grade"] = dict(color_grade)
        return LutGrade(path=Path("tests/fixtures/identity.cube"), interp="tetrahedral")

    monkeypatch.setattr(lf, "resolve_lut_grade", fake_resolve)
    monkeypatch.setattr(lf, "run_ffmpeg", lambda *a, **k: None)
    monkeypatch.setattr(
        lf, "get_correction_for_mode", lambda *a, **k: ""
    )  # isolate: not testing correction

    run_grade(Path("in.mp4"), tmp_path / "out.mp4", load_config(), _cinematic_mode_with_lut())
    assert captured["color_grade"].get("lut_path") == str(
        _IDENTITY_LUT
    )  # mode's color_grade passed
    assert captured["color_grade"].get("enabled") is True


def test_run_grade_fails_loud_when_mode_has_no_lut(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config, load_mode_config
    from src.render.longform import run_grade

    monkeypatch.setattr(
        lf, "run_ffmpeg", lambda *a, **k: pytest.fail("must not encode without a LUT")
    )
    with pytest.raises(ValueError):
        run_grade(
            Path("in.mp4"),
            tmp_path / "out.mp4",
            load_config(),
            load_mode_config("standard_longform"),  # no color_grade block
        )


def test_run_grade_corrects_by_default_over_full_file(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import GRADE_FULL_FILE_SEC, run_grade

    window: dict = {}

    def fake_get(video_path, window_start_sec, window_end_sec, cp_cfg):
        window["start"] = window_start_sec
        window["end"] = window_end_sec
        return "eq=brightness=0.0"

    monkeypatch.setattr(lf, "get_correction_for_mode", fake_get)
    captured: dict = {}
    monkeypatch.setattr(lf, "run_ffmpeg", lambda cmd, *a, **k: captured.__setitem__("cmd", cmd))

    # correction is ON by default — no `correct=` passed
    run_grade(Path("in.mp4"), tmp_path / "out.mp4", load_config(), _cinematic_mode_with_lut())
    assert window["start"] == 0.0
    assert window["end"] == GRADE_FULL_FILE_SEC  # sample to EOF; analyzer clamps to real duration
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert vf.startswith("eq=brightness=0.0,lut3d=")  # correction prepended before the LUT


def test_run_grade_no_correct_flag_omits_correction(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import run_grade

    monkeypatch.setattr(
        lf,
        "get_correction_for_mode",
        lambda *a, **k: pytest.fail("--no-correct must skip correction"),
    )
    captured: dict = {}
    monkeypatch.setattr(lf, "run_ffmpeg", lambda cmd, *a, **k: captured.__setitem__("cmd", cmd))

    run_grade(
        Path("in.mp4"),
        tmp_path / "out.mp4",
        load_config(),
        _cinematic_mode_with_lut(),
        correct=False,
    )
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert vf.startswith("lut3d=")  # LUT only
    assert "eq=" not in vf and "colorbalance" not in vf and "curves" not in vf


def test_run_grade_quality_flows_per_branch(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import run_grade

    cmds: list = []
    monkeypatch.setattr(lf, "run_ffmpeg", lambda cmd, *a, **k: cmds.append(cmd))
    monkeypatch.setattr(lf, "get_correction_for_mode", lambda *a, **k: "")

    # hardware (default): --quality maps to videotoolbox -q:v (no -crf)
    run_grade(
        Path("in.mp4"), tmp_path / "hw.mp4", load_config(), _cinematic_mode_with_lut(), quality=14
    )
    hw = cmds[0]
    assert hw[hw.index("-c:v") + 1] == "h264_videotoolbox"
    assert "-q:v" in hw and "-crf" not in hw
    assert (
        int(hw[hw.index("-q:v") + 1]) > 65
    )  # lower CRF (14) -> higher q:v than the CRF-18 default

    # software: --quality is the libx264 CRF directly
    run_grade(
        Path("in.mp4"),
        tmp_path / "sw.mp4",
        load_config(),
        _cinematic_mode_with_lut(),
        hardware=False,
        quality=16,
    )
    sw = cmds[1]
    assert sw[sw.index("-c:v") + 1] == "libx264"
    assert sw[sw.index("-crf") + 1] == "16" and "-q:v" not in sw


def test_run_grade_bitrate_wins_on_both_branches(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import run_grade

    cmds: list = []
    monkeypatch.setattr(lf, "run_ffmpeg", lambda cmd, *a, **k: cmds.append(cmd))
    monkeypatch.setattr(lf, "get_correction_for_mode", lambda *a, **k: "")

    run_grade(
        Path("in.mp4"),
        tmp_path / "hw.mp4",
        load_config(),
        _cinematic_mode_with_lut(),
        bitrate="50M",
    )  # hardware default
    run_grade(
        Path("in.mp4"),
        tmp_path / "sw.mp4",
        load_config(),
        _cinematic_mode_with_lut(),
        hardware=False,
        bitrate="50M",
    )
    for cmd in cmds:
        assert cmd[cmd.index("-b:v") + 1] == "50M"
        assert "-crf" not in cmd and "-q:v" not in cmd  # bitrate wins over quality on both


def test_run_grade_scale_to_mode_downscales(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import run_grade

    cmds: list = []
    monkeypatch.setattr(lf, "run_ffmpeg", lambda cmd, *a, **k: cmds.append(cmd))
    monkeypatch.setattr(
        lf, "get_correction_for_mode", lambda *a, **k: ""
    )  # isolate: not testing correction
    run_grade(
        Path("in.mp4"),
        tmp_path / "out.mp4",
        load_config(),
        _cinematic_mode_with_lut(),
        scale_to_mode=True,
    )
    vf = cmds[0][cmds[0].index("-vf") + 1]
    assert vf.endswith("scale=1920:1080")


@pytest.mark.integration
def test_run_grade_smoke_produces_native_res_graded_file(tmp_path: Path) -> None:
    import subprocess

    from src.config import load_config
    from src.render.longform import run_grade

    src = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=10:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "graded.mp4"
    result = run_grade(src, out, load_config(), _cinematic_mode_with_lut())
    assert result == out
    assert out.exists() and out.stat().st_size > 0
    probe = ffprobe_json(out)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    assert int(video["width"]) == 640 and int(video["height"]) == 360  # native res preserved
    assert abs(float(probe["format"]["duration"]) - 2.0) < 0.7


# --------------------------------------------------------------------------- #
# `cme grade` hardware (videotoolbox) vs software (libx264) encoder branch.
# Default is hardware on this 8GB M1 (software 4K libx264 exhausts RAM). videotoolbox
# takes -q:v (constant quality), NEVER libx264's -crf/-preset. No silent fallback.
# --------------------------------------------------------------------------- #


def test_crf_to_vt_quality_inversion() -> None:
    from src.render.longform import _crf_to_vt_quality

    assert _crf_to_vt_quality(18) == 65  # tuned 4K-master default
    # lower CRF (higher quality) -> higher videotoolbox q:v
    assert _crf_to_vt_quality(14) > _crf_to_vt_quality(18) > _crf_to_vt_quality(28)
    assert 1 <= _crf_to_vt_quality(51) <= 100 and 1 <= _crf_to_vt_quality(0) <= 100  # clamped


def test_build_grade_command_software_uses_crf_no_vt() -> None:
    args = _grade_cmd(encoder="libx264")
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-crf") + 1] == "18" and args[args.index("-preset") + 1] == "medium"
    assert "-q:v" not in args  # never a videotoolbox quality flag on the software path


def test_build_grade_command_hardware_uses_qv_no_libx264_flags() -> None:
    args = _grade_cmd(encoder="h264_videotoolbox")
    assert args[args.index("-c:v") + 1] == "h264_videotoolbox"
    assert args[args.index("-q:v") + 1] == "65"  # CRF 18 -> q:v 65
    assert "-crf" not in args and "-preset" not in args  # never libx264-only flags to videotoolbox


def test_build_grade_command_hardware_quality_maps_from_crf() -> None:
    hi = _grade_cmd(encoder="h264_videotoolbox", crf=14)
    lo = _grade_cmd(encoder="h264_videotoolbox", crf=28)
    assert int(hi[hi.index("-q:v") + 1]) > 65 > int(lo[lo.index("-q:v") + 1])


def test_build_grade_command_hardware_bitrate_override() -> None:
    args = _grade_cmd(encoder="h264_videotoolbox", bitrate="50M")
    assert args[args.index("-b:v") + 1] == "50M"
    assert "-q:v" not in args and "-crf" not in args and "-preset" not in args


def test_run_grade_hardware_by_default(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import run_grade

    cmds: list = []
    monkeypatch.setattr(lf, "run_ffmpeg", lambda cmd, *a, **k: cmds.append(cmd))
    monkeypatch.setattr(lf, "get_correction_for_mode", lambda *a, **k: "")

    run_grade(Path("in.mp4"), tmp_path / "out.mp4", load_config(), _cinematic_mode_with_lut())
    cmd = cmds[0]
    assert cmd[cmd.index("-c:v") + 1] == "h264_videotoolbox"
    assert "-q:v" in cmd and "-crf" not in cmd and "-preset" not in cmd


def test_run_grade_software_flag_uses_libx264_crf(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import run_grade

    cmds: list = []
    monkeypatch.setattr(lf, "run_ffmpeg", lambda cmd, *a, **k: cmds.append(cmd))
    monkeypatch.setattr(lf, "get_correction_for_mode", lambda *a, **k: "")

    run_grade(
        Path("in.mp4"),
        tmp_path / "out.mp4",
        load_config(),
        _cinematic_mode_with_lut(),
        hardware=False,
    )
    cmd = cmds[0]
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-crf") + 1] == "18" and "-q:v" not in cmd


def test_run_grade_no_silent_fallback_fails_loud(tmp_path: Path, monkeypatch) -> None:
    import src.render.longform as lf
    from src.config import load_config
    from src.render.longform import run_grade
    from src.utils.ffmpeg import FFmpegError

    cmds: list = []

    def boom(cmd, *a, **k):
        cmds.append(cmd)
        raise FFmpegError("hardware encode failed")

    monkeypatch.setattr(lf, "run_ffmpeg", boom)
    monkeypatch.setattr(lf, "get_correction_for_mode", lambda *a, **k: "")

    with pytest.raises(FFmpegError):
        run_grade(Path("in.mp4"), tmp_path / "out.mp4", load_config(), _cinematic_mode_with_lut())
    assert len(cmds) == 1  # no retry
    assert cmds[0][cmds[0].index("-c:v") + 1] == "h264_videotoolbox"
    assert "libx264" not in cmds[0]  # never falls back to the 4K-crashing software path


@pytest.mark.integration
def test_run_grade_software_smoke(tmp_path: Path) -> None:
    import subprocess

    from src.config import load_config
    from src.render.longform import run_grade

    src = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=10:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "graded.mp4"
    run_grade(src, out, load_config(), _cinematic_mode_with_lut(), hardware=False)
    assert out.exists() and out.stat().st_size > 0
    probe = ffprobe_json(out)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    assert int(video["width"]) == 640 and int(video["height"]) == 360
