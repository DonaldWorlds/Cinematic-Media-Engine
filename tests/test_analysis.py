"""Phase 2 analysis-layer tests (written before implementation).

Per the build order in SPEC §14, modules are added one at a time:
librosa → opencv → whisper → hooks → aesthetics. Each writes JSON to
``workspace/[run-id]/analysis/``.

Peak/scene/hook detection logic is unit-tested as pure functions against
synthetic inputs (deterministic, fast). Each module's load-and-analyze entry
point is integration-tested against the synthetic fixtures from conftest.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from src.config import load_config, load_mode_config
from src.ingestion import ingest
from src.utils import fileio

# --------------------------------------------------------------------------- #
# analysis/librosa_analysis — pure peak detection
# --------------------------------------------------------------------------- #


def test_detect_peaks_flat_signal_returns_empty() -> None:
    from src.analysis.librosa_analysis import detect_peaks

    rms = [0.5] * 20
    times = [float(i) for i in range(20)]
    assert detect_peaks(rms, times, threshold_std=1.5, min_gap_sec=3.0) == []


def test_detect_peaks_empty_input_returns_empty() -> None:
    from src.analysis.librosa_analysis import detect_peaks

    assert detect_peaks([], [], threshold_std=1.5, min_gap_sec=3.0) == []


def test_detect_peaks_finds_single_spike() -> None:
    from src.analysis.librosa_analysis import detect_peaks

    rms = [0.1] * 20
    rms[10] = 1.0
    times = [float(i) for i in range(20)]
    peaks = detect_peaks(rms, times, threshold_std=1.5, min_gap_sec=3.0)
    assert len(peaks) == 1
    assert math.isclose(peaks[0].time_sec, 10.0)
    assert peaks[0].rms == 1.0


def test_detect_peaks_merges_neighbors_within_min_gap() -> None:
    from src.analysis.librosa_analysis import detect_peaks

    rms = [0.1] * 20
    rms[10] = 1.0
    rms[11] = 0.9  # 1s away, inside the 3s gap -> suppressed in favor of the taller peak
    times = [float(i) for i in range(20)]
    peaks = detect_peaks(rms, times, threshold_std=1.5, min_gap_sec=3.0)
    assert len(peaks) == 1
    assert math.isclose(peaks[0].time_sec, 10.0)


def test_detect_peaks_keeps_spikes_beyond_min_gap() -> None:
    from src.analysis.librosa_analysis import detect_peaks

    rms = [0.1] * 30
    rms[10] = 1.0
    rms[20] = 0.95  # 10s away, beyond the 3s gap -> kept
    times = [float(i) for i in range(30)]
    peaks = detect_peaks(rms, times, threshold_std=1.5, min_gap_sec=3.0)
    assert len(peaks) == 2
    assert [round(p.time_sec) for p in peaks] == [10, 20]


# --------------------------------------------------------------------------- #
# analysis/librosa_analysis — analyze_audio on the fixture
# --------------------------------------------------------------------------- #


def test_analyze_audio_returns_structured_result(sample_audio: Path) -> None:
    from src.analysis.librosa_analysis import LibrosaAnalysis, analyze_audio

    result = analyze_audio(sample_audio, load_config().audio)
    assert isinstance(result, LibrosaAnalysis)
    assert result.sample_rate == 16000
    assert math.isclose(result.duration_sec, 60.0, abs_tol=1.5)
    assert math.isclose(result.window_sec, 1.0)
    # one signal value per analysis frame; all three curves stay aligned
    assert len(result.energy) > 0
    assert len(result.times_sec) == len(result.energy)
    assert len(result.speech_intensity) == len(result.energy)


def test_analyze_audio_energy_nonnegative_and_speech_unit_range(sample_audio: Path) -> None:
    from src.analysis.librosa_analysis import analyze_audio

    result = analyze_audio(sample_audio, load_config().audio)
    assert result.mean_rms > 0.0  # the fixture carries a 440 Hz tone
    assert all(e >= 0.0 for e in result.energy)
    assert all(0.0 <= s <= 1.0 for s in result.speech_intensity)


def test_analyze_audio_steady_tone_has_no_peaks(sample_audio: Path) -> None:
    from src.analysis.librosa_analysis import analyze_audio

    # A constant-amplitude tone has ~zero energy variance, so nothing should
    # exceed the rolling-mean + 1.5*std threshold. Guards against div-by-zero.
    result = analyze_audio(sample_audio, load_config().audio)
    assert result.peaks == []


def test_write_librosa_json_roundtrips(tmp_path: Path, sample_audio: Path) -> None:
    from src.analysis.librosa_analysis import analyze_audio, write_librosa_json

    result = analyze_audio(sample_audio, load_config().audio)
    out = write_librosa_json(result, tmp_path / "analysis")
    assert out == tmp_path / "analysis" / "librosa.json"
    assert out.exists()
    loaded = fileio.read_json(out)
    assert loaded["sample_rate"] == 16000
    assert len(loaded["energy"]) == len(result.energy)


# --------------------------------------------------------------------------- #
# analysis/opencv — pure frame-motion metric
# --------------------------------------------------------------------------- #


def test_frame_motion_identical_frames_is_zero() -> None:
    from src.analysis.opencv import frame_motion

    frame = np.full((8, 8), 120, dtype=np.uint8)
    assert frame_motion(frame, frame.copy()) == 0.0


def test_frame_motion_black_to_white_is_one() -> None:
    from src.analysis.opencv import frame_motion

    black = np.zeros((8, 8), dtype=np.uint8)
    white = np.full((8, 8), 255, dtype=np.uint8)
    assert math.isclose(frame_motion(black, white), 1.0)


def test_frame_motion_half_changed_is_half() -> None:
    from src.analysis.opencv import frame_motion

    prev = np.zeros((4, 4), dtype=np.uint8)
    curr = np.zeros((4, 4), dtype=np.uint8)
    curr[:2] = 255  # half the pixels flip fully -> mean abs diff = 0.5
    assert math.isclose(frame_motion(prev, curr), 0.5, abs_tol=1e-6)


# --------------------------------------------------------------------------- #
# analysis/opencv — analyze_video on the fixture
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_analyze_video_returns_structured_result(sample_video: Path) -> None:
    from src.analysis.opencv import OpenCVAnalysis, analyze_video

    result = analyze_video(sample_video, load_config().opencv)
    assert isinstance(result, OpenCVAnalysis)
    assert math.isclose(result.duration_sec, 60.0, abs_tol=1.5)
    assert len(result.motion) > 0
    assert len(result.motion_times_sec) == len(result.motion)


@pytest.mark.integration
def test_analyze_video_motion_in_unit_range(sample_video: Path) -> None:
    from src.analysis.opencv import analyze_video

    result = analyze_video(sample_video, load_config().opencv)
    assert all(0.0 <= m <= 1.0 for m in result.motion)
    assert result.mean_motion >= 0.0


@pytest.mark.integration
def test_analyze_video_scene_counts_are_consistent(sample_video: Path) -> None:
    from src.analysis.opencv import analyze_video

    result = analyze_video(sample_video, load_config().opencv)
    # one continuous test pattern -> at least one scene; cuts are the interior
    # boundaries, so scene_count is always cuts + 1.
    assert result.scene_count >= 1
    assert result.scene_count == len(result.scene_cuts_sec) + 1
    assert all(0.0 <= t <= result.duration_sec + 1.0 for t in result.scene_cuts_sec)


@pytest.mark.integration
def test_write_opencv_json_roundtrips(tmp_path: Path, sample_video: Path) -> None:
    from src.analysis.opencv import analyze_video, write_opencv_json

    result = analyze_video(sample_video, load_config().opencv)
    out = write_opencv_json(result, tmp_path / "analysis")
    assert out == tmp_path / "analysis" / "opencv.json"
    loaded = fileio.read_json(out)
    assert loaded["scene_count"] == result.scene_count
    assert len(loaded["motion"]) == len(result.motion)


# --------------------------------------------------------------------------- #
# analysis/whisper — pure transcript assembly (no model)
# --------------------------------------------------------------------------- #


def test_build_transcript_parses_segments_and_words() -> None:
    from src.analysis.whisper import build_transcript

    raw = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": " Hello world ",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.5, "score": 0.9},
                    {"word": "world", "start": 0.6, "end": 1.0, "score": 0.8},
                ],
            },
            {
                "start": 2.0,
                "end": 4.0,
                "text": " yo wait ",
                "words": [
                    {"word": "yo", "start": 2.0, "end": 2.2, "score": 0.7},
                    {"word": "wait", "start": 2.3, "end": 2.8, "score": 0.6},
                ],
            },
        ]
    }
    result = build_transcript(raw, language="en", aligned=True)
    assert result.language == "en"
    assert result.aligned is True
    assert len(result.segments) == 2
    assert result.segments[0].text == "Hello world"
    assert result.text == "Hello world yo wait"
    assert result.word_count == 4
    assert result.segments[0].words[0].word == "Hello"
    assert result.segments[0].words[0].start == 0.0


def test_build_transcript_empty_segments_is_empty_transcript() -> None:
    from src.analysis.whisper import build_transcript

    # VAD on a no-speech clip (e.g. the steady tone fixture) yields no segments.
    result = build_transcript({"segments": []}, language="en", aligned=False)
    assert result.segments == []
    assert result.text == ""
    assert result.word_count == 0
    assert result.aligned is False


def test_build_transcript_tolerates_words_without_timestamps() -> None:
    from src.analysis.whisper import build_transcript

    # Pre-alignment faster-whisper segments carry no per-word start/end.
    raw = {"segments": [{"start": 0.0, "end": 1.0, "text": "hi", "words": [{"word": "hi"}]}]}
    result = build_transcript(raw, language="en", aligned=False)
    word = result.segments[0].words[0]
    assert word.word == "hi"
    assert word.start is None
    assert word.end is None


def test_write_transcript_json_roundtrips(tmp_path: Path) -> None:
    from src.analysis.whisper import build_transcript, write_transcript_json

    result = build_transcript(
        {"segments": [{"start": 0.0, "end": 1.0, "text": "hi", "words": []}]},
        language="en",
        aligned=False,
    )
    out = write_transcript_json(result, tmp_path / "transcript.json")
    assert out == tmp_path / "transcript.json"
    loaded = fileio.read_json(out)
    assert loaded["language"] == "en"
    assert loaded["segments"][0]["text"] == "hi"


# --------------------------------------------------------------------------- #
# analysis/hooks — transcript-based hook + quotable detection (no model)
# --------------------------------------------------------------------------- #


def _transcript(segments: list[dict]) -> object:
    """Build a TranscriptResult from terse segment dicts for hook tests."""
    from src.analysis.whisper import build_transcript

    return build_transcript({"segments": segments}, language="en", aligned=True)


def test_find_hooks_detects_phrase_within_window() -> None:
    from src.analysis.hooks import find_hooks

    transcript = _transcript(
        [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "no way that just happened",
                "words": [
                    {"word": "no", "start": 0.2, "end": 0.4},
                    {"word": "way", "start": 0.4, "end": 0.7},
                    {"word": "that", "start": 0.8, "end": 1.0},
                ],
            }
        ]
    )
    hits = find_hooks(transcript, ["no way", "yo"], window_sec=3.0)
    assert len(hits) == 1
    assert hits[0].phrase == "no way"
    assert hits[0].segment_index == 0
    assert math.isclose(hits[0].time_sec, 0.2)
    assert hits[0].within_window is True


def test_find_hooks_marks_late_phrase_outside_window() -> None:
    from src.analysis.hooks import find_hooks

    transcript = _transcript(
        [
            {
                "start": 10.0,
                "end": 20.0,
                "text": "a long lead in and then hold on",
                "words": [
                    {"word": "a", "start": 10.0, "end": 10.2},
                    {"word": "hold", "start": 18.0, "end": 18.3},
                    {"word": "on", "start": 18.3, "end": 18.6},
                ],
            }
        ]
    )
    hits = find_hooks(transcript, ["hold on"], window_sec=3.0)
    assert len(hits) == 1
    assert hits[0].within_window is False  # 18.0 - 10.0 = 8s, past the 3s window


def test_find_hooks_no_substring_false_positive() -> None:
    from src.analysis.hooks import find_hooks

    # "no way" must not match the single token "norway"
    transcript = _transcript([{"start": 0.0, "end": 2.0, "text": "norway is cold", "words": []}])
    assert find_hooks(transcript, ["no way"], window_sec=3.0) == []


def test_find_hooks_text_fallback_when_no_word_timestamps() -> None:
    from src.analysis.hooks import find_hooks

    transcript = _transcript([{"start": 5.0, "end": 8.0, "text": "yo what is up", "words": []}])
    hits = find_hooks(transcript, ["yo"], window_sec=3.0)
    assert len(hits) == 1
    assert math.isclose(hits[0].time_sec, 5.0)
    assert hits[0].within_window is True


def test_find_quotables_filters_short_segments() -> None:
    from src.analysis.hooks import find_quotables

    transcript = _transcript(
        [
            {"start": 0.0, "end": 1.0, "text": "yeah", "words": []},
            {
                "start": 1.0,
                "end": 6.0,
                "text": "this is the realest thing i ever said",
                "words": [],
            },
        ]
    )
    quotables = find_quotables(transcript, min_words=5)
    assert len(quotables) == 1
    assert quotables[0].text == "this is the realest thing i ever said"
    assert quotables[0].word_count == 8


def test_analyze_hooks_writes_json_roundtrip(tmp_path: Path) -> None:
    from src.analysis.hooks import HooksAnalysis, analyze_hooks, write_hooks_json

    transcript = _transcript(
        [{"start": 0.0, "end": 4.0, "text": "yo this is actually wild stuff here", "words": []}]
    )
    cfg = load_config().hooks
    analysis = analyze_hooks(transcript, cfg)
    assert isinstance(analysis, HooksAnalysis)
    assert analysis.hook_count == len(analysis.hooks)
    assert analysis.quotable_count == len(analysis.quotables)
    out = write_hooks_json(analysis, tmp_path / "analysis")
    assert out == tmp_path / "analysis" / "hooks.json"
    loaded = fileio.read_json(out)
    assert loaded["hook_count"] == analysis.hook_count


# --------------------------------------------------------------------------- #
# analysis/aesthetics — pure per-frame metrics
# --------------------------------------------------------------------------- #


def test_lighting_quality_peaks_at_mid_exposure() -> None:
    from src.analysis.aesthetics import lighting_quality

    mid = np.full((8, 8), 127, dtype=np.uint8)
    black = np.zeros((8, 8), dtype=np.uint8)
    white = np.full((8, 8), 255, dtype=np.uint8)
    assert lighting_quality(mid) > 0.95
    assert lighting_quality(black) < 0.05
    assert lighting_quality(white) < 0.05


def test_sharpness_zero_on_flat_positive_on_edges() -> None:
    from src.analysis.aesthetics import sharpness

    flat = np.full((16, 16), 100, dtype=np.uint8)
    checker = np.zeros((16, 16), dtype=np.uint8)
    checker[::2, ::2] = 255
    checker[1::2, 1::2] = 255
    assert sharpness(flat) == 0.0
    assert sharpness(checker) > 0.0


def test_colorfulness_low_for_gray_higher_for_saturated() -> None:
    from src.analysis.aesthetics import colorfulness

    gray = np.full((8, 8, 3), 120, dtype=np.uint8)
    red = np.zeros((8, 8, 3), dtype=np.uint8)
    red[..., 2] = 255  # BGR -> red channel
    assert colorfulness(gray) < 0.05
    assert colorfulness(red) > colorfulness(gray)


# --------------------------------------------------------------------------- #
# analysis/aesthetics — analyze_aesthetics on the fixture
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_analyze_aesthetics_returns_metrics_in_unit_range(sample_video: Path) -> None:
    from src.analysis.aesthetics import AestheticsAnalysis, analyze_aesthetics

    cfg = load_config().aesthetics
    result = analyze_aesthetics(sample_video, cfg)
    assert isinstance(result, AestheticsAnalysis)
    assert result.frame_count > 0
    for name in cfg.metrics:
        assert name in result.means
        assert 0.0 <= result.means[name] <= 1.0
        assert len(result.metrics[name]) == result.frame_count


@pytest.mark.integration
def test_write_aesthetics_json_roundtrips(tmp_path: Path, sample_video: Path) -> None:
    from src.analysis.aesthetics import analyze_aesthetics, write_aesthetics_json

    result = analyze_aesthetics(sample_video, load_config().aesthetics)
    out = write_aesthetics_json(result, tmp_path / "analysis")
    assert out == tmp_path / "analysis" / "aesthetics.json"
    loaded = fileio.read_json(out)
    assert loaded["frame_count"] == result.frame_count
    assert set(loaded["means"].keys()) == set(result.means.keys())


# --------------------------------------------------------------------------- #
# analysis orchestration — run_analysis + mode-conditional transcription
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mode_name", "override", "expected"),
    [
        ("shortform", False, True),  # shortform always transcribes
        ("standard_longform", False, False),  # off by default
        ("standard_longform", True, True),  # --transcribe forces it
        ("cinematic_longform", False, False),
    ],
)
def test_should_transcribe_mode_gating(mode_name: str, override: bool, expected: bool) -> None:
    from src.analysis import should_transcribe

    mode = load_mode_config(mode_name)
    assert should_transcribe(mode, transcribe_override=override) is expected


@pytest.mark.integration
def test_run_analysis_writes_local_artifacts_without_transcription(
    tmp_path: Path, sample_video: Path
) -> None:
    from src.analysis import run_analysis

    cfg = load_config()
    cfg.workspace.root = tmp_path / "workspace"
    workspace = ingest(sample_video, cfg)
    mode = load_mode_config("standard_longform")  # transcription off by default

    outputs = run_analysis(sample_video, workspace, cfg, mode, transcribe_override=False)

    assert outputs.transcribed is False
    assert outputs.transcript_path is None
    assert outputs.hooks_path is None
    assert outputs.librosa_path.exists()
    assert outputs.opencv_path is not None and outputs.opencv_path.exists()
    assert outputs.aesthetics_path is not None and outputs.aesthetics_path.exists()
    for name in ("librosa.json", "opencv.json", "aesthetics.json"):
        assert (workspace.analysis_dir / name).exists()
    assert not workspace.transcript_path.exists()


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("CME_RUN_WHISPER_MODEL") != "1",
    reason="set CME_RUN_WHISPER_MODEL=1 to download (~800MB) and run the real WhisperX stack",
)
def test_transcribe_audio_real_model(sample_audio: Path) -> None:
    from src.analysis.whisper import TranscriptResult, transcribe_audio

    result = transcribe_audio(sample_audio, load_config().transcription)
    assert isinstance(result, TranscriptResult)
    assert result.language == "en"
    # the 440 Hz tone has no speech; VAD should yield an empty transcript, not a crash
    assert isinstance(result.segments, list)


# --------------------------------------------------------------------------- #
# whisper — alignment device selection + MPS->CPU fallback (Phase-3 long-audio fix)
# --------------------------------------------------------------------------- #


def test_select_alignment_device_env_var_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.analysis.whisper import _select_alignment_device

    monkeypatch.setenv("CME_ALIGNMENT_DEVICE", "cpu")
    assert _select_alignment_device("mps") == "cpu"


def test_select_alignment_device_uses_config_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.analysis.whisper import _select_alignment_device

    monkeypatch.delenv("CME_ALIGNMENT_DEVICE", raising=False)
    assert _select_alignment_device("cpu") == "cpu"  # explicit config device passes through


def _fake_load(*, language_code: str, device: str) -> tuple[str, str]:
    return f"model-{device}", f"meta-{device}"


def test_align_transcript_no_fallback_when_device_succeeds() -> None:
    from src.analysis.whisper import align_transcript

    calls: list[str] = []

    def fake_align(segments, model, metadata, audio, device, return_char_alignments):  # type: ignore[no-untyped-def]
        calls.append(device)
        return {"segments": segments, "word_segments": []}

    raw, used = align_transcript(
        [{"start": 0.0, "end": 1.0, "text": "hi", "words": []}],
        audio=[0.0],
        language_code="en",
        device="mps",
        load_align_model=_fake_load,
        align=fake_align,
    )
    assert used == "mps"
    assert calls == ["mps"]
    assert "segments" in raw


def test_align_transcript_falls_back_to_cpu_on_mps_not_implemented() -> None:
    from src.analysis.whisper import align_transcript

    calls: list[str] = []

    def fake_align(segments, model, metadata, audio, device, return_char_alignments):  # type: ignore[no-untyped-def]
        calls.append(device)
        if device == "mps":
            raise NotImplementedError("Output channels > 65536 not supported at the MPS device")
        return {"segments": segments, "word_segments": []}

    _, used = align_transcript(
        [{"start": 0.0, "end": 1.0, "text": "hi", "words": []}],
        audio=[0.0],
        language_code="en",
        device="mps",
        load_align_model=_fake_load,
        align=fake_align,
    )
    assert used == "cpu"
    assert calls == ["mps", "cpu"]


def test_align_transcript_falls_back_on_runtime_error() -> None:
    from src.analysis.whisper import align_transcript

    def fake_align(segments, model, metadata, audio, device, return_char_alignments):  # type: ignore[no-untyped-def]
        if device == "mps":
            raise RuntimeError("MPS backend out of memory")
        return {"segments": segments}

    _, used = align_transcript(
        [{"start": 0.0, "end": 1.0, "text": "hi", "words": []}],
        audio=[0.0],
        language_code="en",
        device="mps",
        load_align_model=_fake_load,
        align=fake_align,
    )
    assert used == "cpu"


def test_align_transcript_reraises_when_cpu_also_fails() -> None:
    from src.analysis.whisper import align_transcript

    def fake_align(segments, model, metadata, audio, device, return_char_alignments):  # type: ignore[no-untyped-def]
        raise NotImplementedError("boom on every device")

    with pytest.raises(NotImplementedError):
        align_transcript(
            [{"start": 0.0, "end": 1.0, "text": "hi", "words": []}],
            audio=[0.0],
            language_code="en",
            device="mps",
            load_align_model=_fake_load,
            align=fake_align,
        )


# --------------------------------------------------------------------------- #
# subjects (Phase 4.5 Layer 1) — pure content-profile math from person boxes
# --------------------------------------------------------------------------- #


def _box(x1: float, x2: float):
    from src.analysis.subjects import SubjectBox

    return SubjectBox(x1=x1, y1=100.0, x2=x2, y2=1000.0, confidence=0.9)


def test_union_bbox_spans_all_boxes() -> None:
    from src.analysis.subjects import SubjectBox, union_bbox

    boxes = [
        SubjectBox(x1=100, y1=50, x2=300, y2=900, confidence=0.9),
        SubjectBox(x1=500, y1=80, x2=800, y2=950, confidence=0.8),
    ]
    assert union_bbox(boxes) == (100.0, 50.0, 800.0, 950.0)


def test_union_bbox_empty_is_none() -> None:
    from src.analysis.subjects import union_bbox

    assert union_bbox([]) is None


def test_spread_ratio_is_fraction_of_frame_width() -> None:
    from src.analysis.subjects import spread_ratio

    # boxes span x 100..800 -> width 700 of a 1920px frame
    assert math.isclose(spread_ratio([_box(100, 300), _box(500, 800)], 1920), 700 / 1920)


def test_spread_ratio_empty_is_zero() -> None:
    from src.analysis.subjects import spread_ratio

    assert spread_ratio([], 1920) == 0.0


def test_profile_frame_solo() -> None:
    from src.analysis.subjects import profile_frame

    p = profile_frame([_box(800, 1100)], frame_width=1920, time_sec=5.0)
    assert p.subject_count == 1
    assert p.is_solo is True
    assert math.isclose(p.spread_ratio, 300 / 1920)
    assert p.group_bbox == [800.0, 100.0, 1100.0, 1000.0]
    assert p.time_sec == 5.0


def test_profile_frame_empty_has_no_subjects() -> None:
    from src.analysis.subjects import profile_frame

    p = profile_frame([], frame_width=1920, time_sec=0.0)
    assert p.subject_count == 0
    assert p.is_solo is False
    assert p.spread_ratio == 0.0
    assert p.group_bbox is None


def test_profile_frame_multiple_not_solo_and_wide() -> None:
    from src.analysis.subjects import profile_frame

    boxes = [_box(50, 300), _box(700, 950), _box(1500, 1850)]
    p = profile_frame(boxes, frame_width=1920, time_sec=1.0)
    assert p.subject_count == 3
    assert p.is_solo is False
    assert math.isclose(p.spread_ratio, (1850 - 50) / 1920)  # wide spread


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("CME_RUN_YOLO_MODEL") != "1",
    reason="set CME_RUN_YOLO_MODEL=1 to download (~6MB) and run the real YOLO model",
)
def test_detect_subjects_real_model(sample_video: Path) -> None:
    from src.analysis.subjects import SubjectsAnalysis, detect_subjects
    from src.config import load_config

    result = detect_subjects(sample_video, load_config().subjects)
    # the synthetic test pattern has no people; the path must still run and profile frames
    assert isinstance(result, SubjectsAnalysis)
    assert result.frame_width == 1920
    assert len(result.frames) > 0
    assert all(f.subject_count >= 0 for f in result.frames)


# --------------------------------------------------------------------------- #
# Phase 6 — analysis caching (skip-if-cached, per-artifact, --fresh) + two-layer
# safety. Written before implementation.
# --------------------------------------------------------------------------- #


def _cache_cfg(tmp_path):
    from src.config import load_config

    cfg = load_config()
    cfg.workspace.root = tmp_path / "ws"
    cfg.subjects.enabled = False  # skip YOLO to keep these fast (cache mechanism is the point)
    return cfg


def _raise(*a, **k):
    raise AssertionError("this stage should have been served from cache")


@pytest.mark.integration
def test_cache_hit_skips_recompute(tmp_path, sample_video, monkeypatch) -> None:
    from src.analysis import run_analysis
    from src.config import load_mode_config
    from src.ingestion import ingest

    cfg = _cache_cfg(tmp_path)
    mode = load_mode_config("standard_longform")
    ws = ingest(sample_video, cfg)
    run_analysis(sample_video, ws, cfg, mode)  # populate cache + manifest

    monkeypatch.setattr("src.analysis.librosa_analysis.analyze_audio", _raise)
    monkeypatch.setattr("src.analysis.opencv.analyze_video", _raise)
    monkeypatch.setattr("src.analysis.aesthetics.analyze_aesthetics", _raise)

    out = run_analysis(sample_video, ws, cfg, mode)
    assert out.from_cache == {"librosa", "opencv", "aesthetics"}


@pytest.mark.integration
def test_stale_artifact_recomputes_only_itself(tmp_path, sample_video, monkeypatch) -> None:
    import src.analysis.opencv as ocv
    from src.analysis import run_analysis
    from src.config import load_mode_config
    from src.ingestion import ingest

    cfg = _cache_cfg(tmp_path)
    mode = load_mode_config("standard_longform")
    ws = ingest(sample_video, cfg)
    run_analysis(sample_video, ws, cfg, mode)

    # invalidate ONLY the opencv-relevant config
    cfg.opencv.scene_detect_threshold = 99.0

    real_video = ocv.analyze_video
    ran = {"opencv": False}

    def spy(*a, **k):
        ran["opencv"] = True
        return real_video(*a, **k)

    monkeypatch.setattr(ocv, "analyze_video", spy)
    monkeypatch.setattr("src.analysis.librosa_analysis.analyze_audio", _raise)
    monkeypatch.setattr("src.analysis.aesthetics.analyze_aesthetics", _raise)

    out = run_analysis(sample_video, ws, cfg, mode)
    assert ran["opencv"] is True
    assert "opencv" not in out.from_cache
    assert {"librosa", "aesthetics"} <= out.from_cache


@pytest.mark.integration
def test_fresh_flag_forces_full_recompute(tmp_path, sample_video) -> None:
    from src.analysis import run_analysis
    from src.config import load_mode_config
    from src.ingestion import ingest

    cfg = _cache_cfg(tmp_path)
    mode = load_mode_config("standard_longform")
    ws = ingest(sample_video, cfg)
    run_analysis(sample_video, ws, cfg, mode)

    out = run_analysis(sample_video, ws, cfg, mode, fresh=True)
    assert out.from_cache == set()


@pytest.mark.integration
def test_two_layer_discovery_then_fingerprint_invalidation(
    tmp_path, sample_video, monkeypatch
) -> None:
    """Discovery finds the workspace by path, but a changed source forces re-analysis."""
    import src.analysis.librosa_analysis as lib
    from src.analysis import run_analysis
    from src.config import load_mode_config
    from src.ingestion import ingest
    from src.ingestion.workspace import find_workspace_for_source

    cfg = _cache_cfg(tmp_path)
    mode = load_mode_config("standard_longform")
    src = tmp_path / "clip.mp4"
    src.write_bytes(sample_video.read_bytes())
    ws = ingest(src, cfg)
    run_analysis(src, ws, cfg, mode)  # cache populated for the original source

    # mutate the source file (size change -> fingerprint mismatch)
    with open(src, "ab") as f:
        f.write(b"\x00" * 4096)

    found = find_workspace_for_source(src, cfg.workspace.root)
    assert found is not None  # layer 1: path match still finds the workspace

    real = lib.analyze_audio
    ran = {"librosa": False}

    def spy(*a, **k):
        ran["librosa"] = True
        return real(*a, **k)

    monkeypatch.setattr(lib, "analyze_audio", spy)
    out = run_analysis(src, found, cfg, mode)
    assert ran["librosa"] is True  # layer 2: fingerprint invalidated the cache
    assert "librosa" not in out.from_cache


def test_analysis_parallelism_overlaps_light_with_heavy(
    tmp_path, sample_video, monkeypatch
) -> None:
    """A light stage runs concurrently with the long heavy (whisper) stage, and the two
    heavy stages (whisper, subjects) never co-run (8GB heavy-lane cap = 1)."""
    import threading
    import time

    from src.analysis import run_analysis
    from src.config import load_config, load_mode_config
    from src.ingestion import ingest

    cfg = load_config()
    cfg.workspace.root = tmp_path / "ws"
    mode = load_mode_config("shortform")  # transcription ON
    ws = ingest(sample_video, cfg)

    spans: dict[str, list[float]] = {}
    lock = threading.Lock()
    t0 = time.monotonic()

    def stub(name: str, secs: float):
        def fn(*a, **k):
            with lock:
                spans[name] = [time.monotonic() - t0, 0.0]
            time.sleep(secs)
            with lock:
                spans[name][1] = time.monotonic() - t0
            return object()  # opaque payload; the write_* stubs ignore it

        return fn

    monkeypatch.setattr("src.analysis.librosa_analysis.analyze_audio", stub("librosa", 0.02))
    monkeypatch.setattr("src.analysis.opencv.analyze_video", stub("opencv", 0.10))
    monkeypatch.setattr("src.analysis.aesthetics.analyze_aesthetics", stub("aesthetics", 0.02))
    monkeypatch.setattr("src.analysis.subjects.detect_subjects", stub("subjects", 0.10))
    monkeypatch.setattr("src.analysis.whisper.transcribe_audio", stub("whisper", 0.20))
    monkeypatch.setattr("src.analysis.hooks.analyze_hooks", stub("hooks", 0.01))
    for modpath, fnname in [
        ("src.analysis.librosa_analysis", "write_librosa_json"),
        ("src.analysis.opencv", "write_opencv_json"),
        ("src.analysis.aesthetics", "write_aesthetics_json"),
        ("src.analysis.subjects", "write_subjects_json"),
        ("src.analysis.hooks", "write_hooks_json"),
    ]:
        monkeypatch.setattr(f"{modpath}.{fnname}", lambda *a, **k: ws.analysis_dir / "x.json")

    # hooks reloads the transcript from disk (workspace-as-contract), so the whisper
    # write stub must produce a minimal valid transcript.json for it to parse.
    def _write_min_transcript(*a, **k):
        ws.transcript_path.write_text(
            '{"language":"en","text":"","segments":[],"word_count":0,"aligned":true}',
            encoding="utf-8",
        )
        return ws.transcript_path

    monkeypatch.setattr("src.analysis.whisper.write_transcript_json", _write_min_transcript)

    run_analysis(sample_video, ws, cfg, mode, fresh=True)

    def overlaps(a: str, b: str) -> bool:
        sa, ea = spans[a]
        sb, eb = spans[b]
        return sa < eb and sb < ea

    # hooks depends on whisper -> starts only after whisper ends
    assert spans["hooks"][0] >= spans["whisper"][1] - 1e-3
    # a light stage overlapped the long whisper stage -> real parallelism
    assert overlaps("opencv", "whisper")
    # the two heavy stages did NOT overlap (heavy lane cap = 1)
    assert not overlaps("whisper", "subjects")
