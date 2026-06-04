"""Phase 9 — Descript handoff automation (mocked API).

Tests the upload -> one combined Underlord prompt -> publish -> download flow and its
CLI dispatch. The Descript REST API is mocked at the HTTP boundary only (no network):
``handoff.requests`` is monkeypatched with a queue of ``FakeResponse`` objects, and the
clock (``handoff.time``) is patched for deterministic polling/timeout/retry. Assertions
target OUR behavior — the requests we build, the responses we parse, the errors we raise,
the files we select/name/write — never merely "a mock was called".
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from src import cli
from src.config import DescriptSettings, ModeConfig, load_mode_config
from src.descript import auth as descript_auth
from src.descript import handoff
from src.descript.handoff import (
    DescriptError,
    RateLimitError,
    _resolve_composition,
    build_polished_path,
    dispatch_polish,
    download_polished,
    import_media,
    polish_video,
    publish_project,
    run_underlord,
    upload_to_descript,
    wait_for_job,
)

# --------------------------------------------------------------------------- #
# HTTP test harness — FakeResponse queue + recorder, deterministic clock
# --------------------------------------------------------------------------- #


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict | None = None,
        headers: dict | None = None,
        chunks: list[bytes] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self._chunks = chunks or []
        self.text = text

    def json(self) -> dict:
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def iter_content(self, chunk_size: int = 8192):
        yield from self._chunks


class FakeRequests:
    """Replaces handoff.requests; serves queued responses, records each call."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def http(monkeypatch):
    """Install a FakeRequests with the given responses and freeze the clock.

    Returns a factory; call ``http([resp, ...])`` to arm responses. The clock is
    frozen at 0 (never times out) and sleeps are recorded into ``slept``.
    """
    slept: list[float] = []
    monkeypatch.setattr(handoff.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(handoff.time, "monotonic", lambda: 0.0)

    def factory(responses: list) -> FakeRequests:
        fake = FakeRequests(responses)
        monkeypatch.setattr(handoff, "requests", fake)
        return fake

    factory.slept = slept  # type: ignore[attr-defined]
    return factory


def _job(state: str, status: str | None = None, **result) -> FakeResponse:
    body: dict = {"job_state": state}
    if status is not None:
        body["result"] = {"status": status, **result}
    return FakeResponse(200, body)


# --------------------------------------------------------------------------- #
# A. auth — load_descript_token
# --------------------------------------------------------------------------- #


def test_load_token_returns_env_value(monkeypatch):
    monkeypatch.setenv("DESCRIPT_API_KEY", "tok-abc")
    assert descript_auth.load_descript_token() == "tok-abc"


def test_missing_token_raises_with_setup_instructions(monkeypatch):
    monkeypatch.setattr(descript_auth, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DESCRIPT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DESCRIPT_API_KEY"):
        descript_auth.load_descript_token()


def test_blank_token_treated_as_missing(monkeypatch):
    monkeypatch.setattr(descript_auth, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DESCRIPT_API_KEY", "   ")
    with pytest.raises(RuntimeError):
        descript_auth.load_descript_token()


# --------------------------------------------------------------------------- #
# B. HTTP error mapping (fail-loud) — tested at the _api seam
# --------------------------------------------------------------------------- #


def test_401_raises_auth_error(http):
    http([FakeResponse(401)])
    with pytest.raises(DescriptError, match=r"(?i)token|auth|401"):
        handoff._api("GET", "/projects/x", token="tok")


def test_402_import_raises_media_minutes_error(http):
    http([FakeResponse(402)])
    with pytest.raises(DescriptError, match=r"(?i)media minutes"):
        handoff._api("POST", "/jobs/import/project_media", token="tok", json={})


def test_402_agent_raises_credits_error(http):
    http([FakeResponse(402)])
    with pytest.raises(DescriptError, match=r"(?i)credit"):
        handoff._api("POST", "/jobs/agent", token="tok", json={})


def test_403_agent_raises_agent_disabled_error(http):
    http([FakeResponse(403)])
    with pytest.raises(DescriptError, match=r"(?i)agent"):
        handoff._api("POST", "/jobs/agent", token="tok", json={})


def test_404_raises_not_found(http):
    http([FakeResponse(404)])
    with pytest.raises(DescriptError, match=r"(?i)not found|404"):
        handoff._api("GET", "/jobs/missing", token="tok")


def test_429_honors_retry_after_then_succeeds(http):
    fake = http(
        [
            FakeResponse(429, headers={"Retry-After": "2"}),
            FakeResponse(200, {"ok": True}),
        ]
    )
    out = handoff._api("GET", "/projects/x", token="tok")
    assert out == {"ok": True}
    assert http.slept == [2.0]
    assert len(fake.calls) == 2


def test_429_max_retries_then_raises(http):
    # 4 consecutive 429s -> 3 retries then give up (max retries pinned at 3).
    http([FakeResponse(429, headers={"Retry-After": "1"})] * 4)
    with pytest.raises(RateLimitError):
        handoff._api("GET", "/projects/x", token="tok")
    assert len(http.slept) == 3


# --------------------------------------------------------------------------- #
# C. upload_to_descript + import_media
# --------------------------------------------------------------------------- #


def _import_ok(key: str, project_id: str = "P1", job_id: str = "Jimp") -> FakeResponse:
    return FakeResponse(
        201,
        {
            "job_id": job_id,
            "project_id": project_id,
            "drive_id": "D1",
            "upload_urls": {
                key: {"upload_url": "https://signed/put", "asset_id": "a", "artifact_id": "b"}
            },
        },
    )


def test_upload_posts_import_with_correct_body(http, tmp_path):
    video = tmp_path / "cut.mp4"
    video.write_bytes(b"VIDEODATA")
    fake = http([_import_ok("cut.mp4"), FakeResponse(200), _job("stopped", "success")])
    upload_to_descript(video, "Proj", token="tok")
    post = fake.calls[0]
    assert post["method"] == "POST"
    assert post["url"].endswith("/jobs/import/project_media")
    body = post["json"]
    assert body["project_name"] == "Proj"
    spec = body["add_media"]["cut.mp4"]
    assert spec["content_type"] == "video/mp4"


def test_upload_file_size_equals_real_bytes(http, tmp_path):
    video = tmp_path / "cut.mp4"
    video.write_bytes(b"X" * 1234)
    fake = http([_import_ok("cut.mp4"), FakeResponse(200), _job("stopped", "success")])
    upload_to_descript(video, "Proj", token="tok")
    assert fake.calls[0]["json"]["add_media"]["cut.mp4"]["file_size"] == 1234


def test_upload_puts_bytes_with_octet_stream(http, tmp_path):
    video = tmp_path / "cut.mp4"
    video.write_bytes(b"RAWBYTES")
    fake = http([_import_ok("cut.mp4"), FakeResponse(200), _job("stopped", "success")])
    upload_to_descript(video, "Proj", token="tok")
    put = fake.calls[1]
    assert put["method"] == "PUT"
    assert put["url"] == "https://signed/put"
    assert put["headers"]["Content-Type"] == "application/octet-stream"
    assert put["data"] == b"RAWBYTES"


def test_upload_waits_for_import_then_returns_project_id(http, tmp_path):
    video = tmp_path / "cut.mp4"
    video.write_bytes(b"V")
    http(
        [_import_ok("cut.mp4", project_id="PROJ-9"), FakeResponse(200), _job("stopped", "success")]
    )
    assert upload_to_descript(video, "Proj", token="tok") == "PROJ-9"


def test_upload_import_failure_raises(http, tmp_path):
    video = tmp_path / "cut.mp4"
    video.write_bytes(b"V")
    http([_import_ok("cut.mp4"), FakeResponse(200), _job("stopped", "error")])
    with pytest.raises(RuntimeError):
        upload_to_descript(video, "Proj", token="tok")


def test_import_media_adds_to_existing_project(http, tmp_path):
    music = tmp_path / "bed.mp3"
    music.write_bytes(b"MP3")
    fake = http([_import_ok("bed.mp3"), FakeResponse(200), _job("stopped", "success")])
    import_media("PROJ-9", music, token="tok")
    body = fake.calls[0]["json"]
    assert body["project_id"] == "PROJ-9"
    assert "project_name" not in body
    assert "bed.mp3" in body["add_media"]


def test_upload_places_clip_on_composition(http, tmp_path):
    # WITHOUT add_compositions Descript leaves the clip in the media pool with an EMPTY
    # composition (dur 0) -> Underlord has nothing to edit and publish fails (live test 2026-06-01).
    video = tmp_path / "cut.mp4"
    video.write_bytes(b"V")
    fake = http([_import_ok("cut.mp4"), FakeResponse(200), _job("stopped", "success")])
    upload_to_descript(video, "Proj", token="tok")
    body = fake.calls[0]["json"]
    assert body["add_compositions"] == [{"name": "Proj", "clips": [{"media": "cut.mp4"}]}]


def test_import_media_does_not_create_composition(http, tmp_path):
    # Music added to an EXISTING project stays in the pool for Underlord to place — no new composition.
    music = tmp_path / "bed.mp3"
    music.write_bytes(b"M")
    fake = http([_import_ok("bed.mp3"), FakeResponse(200), _job("stopped", "success")])
    import_media("PROJ-9", music, token="tok")
    assert "add_compositions" not in fake.calls[0]["json"]


# --------------------------------------------------------------------------- #
# D. _resolve_composition
# --------------------------------------------------------------------------- #


def test_resolve_returns_first_composition_id(http):
    # Two compositions present -> we take the first (documented decision; single-video
    # import is expected to yield exactly one — see TODO(live-test) in handoff).
    http([FakeResponse(200, {"compositions": [{"id": "C-first"}, {"id": "C-second"}]})])
    assert _resolve_composition("PROJ-9", token="tok") == "C-first"


def test_resolve_no_compositions_raises(http):
    http([FakeResponse(200, {"compositions": []})])
    with pytest.raises(DescriptError):
        _resolve_composition("PROJ-9", token="tok")


# --------------------------------------------------------------------------- #
# E. run_underlord
# --------------------------------------------------------------------------- #


def test_posts_agent_with_project_id_and_prompt(http):
    fake = http([FakeResponse(201, {"job_id": "Jagent"})])
    assert run_underlord("PROJ-9", "Apply Studio Sound.", token="tok") == "Jagent"
    body = fake.calls[0]["json"]
    assert fake.calls[0]["url"].endswith("/jobs/agent")
    assert body["project_id"] == "PROJ-9"
    assert "project_name" not in body


def test_prompt_sent_verbatim(http):
    prompt = "Apply eye contact correction. Apply Studio Sound. Apply color adjustments."
    fake = http([FakeResponse(201, {"job_id": "Jagent"})])
    run_underlord("PROJ-9", prompt, token="tok")
    assert fake.calls[0]["json"]["prompt"] == prompt
    assert len(fake.calls) == 1


# --------------------------------------------------------------------------- #
# F. wait_for_job — two-level success, timeout, polling interval
# --------------------------------------------------------------------------- #


def test_polls_until_stopped_success_returns_result(http):
    http([_job("running"), _job("stopped", "success", ai_credits_used=32)])
    result = wait_for_job("Jx", token="tok")
    assert result["status"] == "success"
    assert result["ai_credits_used"] == 32


def test_stopped_failure_raises_runtimeerror(http):
    # A 'stopped' job is terminal but NOT necessarily successful — must inspect result.status.
    http([_job("stopped", "error")])
    with pytest.raises(RuntimeError):
        wait_for_job("Jx", token="tok")


def test_timeout_raises_timeouterror(monkeypatch):
    monkeypatch.setattr(handoff.time, "sleep", lambda s: None)
    ticks = iter([0.0] + [float(t) for t in range(100, 100000, 100)])
    monkeypatch.setattr(handoff.time, "monotonic", lambda: next(ticks))

    class AlwaysRunning:
        def request(self, *a, **k):
            return _job("running")

    monkeypatch.setattr(handoff, "requests", AlwaysRunning())
    with pytest.raises(TimeoutError):
        wait_for_job("Jx", token="tok", timeout_sec=900)


def test_respects_polling_interval(http):
    http([_job("running"), _job("stopped", "success")])
    wait_for_job("Jx", token="tok", polling_interval_sec=7)
    assert 7 in http.slept


# --------------------------------------------------------------------------- #
# G. publish_project + download_polished
# --------------------------------------------------------------------------- #


def test_publish_posts_with_project_composition_resolution(http):
    fake = http(
        [
            FakeResponse(201, {"job_id": "Jpub"}),
            _job("stopped", "success", download_url="https://dl/x"),
        ]
    )
    publish_project("PROJ-9", "C-first", "1080p", token="tok")
    body = fake.calls[0]["json"]
    assert fake.calls[0]["url"].endswith("/jobs/publish")
    assert body["project_id"] == "PROJ-9"
    assert body["composition_id"] == "C-first"
    assert body["resolution"] == "1080p"


def test_publish_waits_and_returns_download_url(http):
    http(
        [
            FakeResponse(201, {"job_id": "Jpub"}),
            _job("running"),
            _job("stopped", "success", download_url="https://dl/final.mp4"),
        ]
    )
    assert publish_project("PROJ-9", "C-first", token="tok") == "https://dl/final.mp4"


def test_publish_resolution_cap_403_raises_clear_error(http):
    http([FakeResponse(403)])
    with pytest.raises(DescriptError, match=r"(?i)resolution|plan"):
        publish_project("PROJ-9", "C-first", "4K", token="tok")


def test_download_streams_to_output_path(http, tmp_path):
    out = tmp_path / "polished" / "out.mp4"
    http([FakeResponse(200, chunks=[b"AAA", b"BBB", b"CCC"])])
    returned = download_polished("https://dl/final.mp4", out)
    assert returned == out
    assert out.read_bytes() == b"AAABBBCCC"


def test_download_expired_url_raises_clear_error(http, tmp_path):
    http([FakeResponse(403)])
    with pytest.raises(DescriptError, match=r"(?i)expire|forbidden|signed"):
        download_polished("https://dl/expired", tmp_path / "out.mp4")


# --------------------------------------------------------------------------- #
# H. config — DescriptSettings / ModeConfig.descript
# --------------------------------------------------------------------------- #


def test_descript_settings_defaults():
    ds = DescriptSettings()
    assert ds.enabled is False
    assert ds.prompt == ""
    assert ds.publish_resolution == "1080p"
    assert ds.polling_interval_sec == 10
    assert ds.timeout_sec == 900
    assert ds.music_path is None


def test_publish_resolution_literal_rejects_bad_value():
    with pytest.raises(ValueError):
        DescriptSettings(publish_resolution="2160p")


def test_modeconfig_without_descript_defaults_disabled():
    mode = ModeConfig.model_validate(
        {
            "mode_name": "x",
            "transcription": {"enabled": False},
            "scoring_weights": {"a": 1.0},
            "pacing": {},
            "rendering": {},
            "output": {"base_dir": "src/output/x"},
            "clip_density": "low",
        }
    )
    assert mode.descript.enabled is False


def test_shortform_yaml_has_no_descript_block():
    # Phase 9: shortform is NOT polished — clips inherit polish from their source longform.
    mode = load_mode_config("shortform")
    assert mode.descript.enabled is False


def test_cinematic_yaml_has_music_path():
    # RED until batch C.
    mode = load_mode_config("cinematic_longform")
    assert mode.descript.enabled is True
    assert str(mode.descript.music_path).endswith("cinematic_bed.mp3")


def test_longform_prompts_have_no_captions():
    # Phase 9.1: captions are shortform-only (engine burns them via ffmpeg). Longform and
    # cinematic stay caption-free — Descript must not add captions.
    for mode_name in ("standard_longform", "cinematic_longform"):
        prompt = load_mode_config(mode_name).descript.prompt.lower()
        assert "caption" not in prompt, f"{mode_name} prompt should not request captions"


# --------------------------------------------------------------------------- #
# J. build_polished_path + orchestrator polish_video
# --------------------------------------------------------------------------- #


def test_output_naming_and_polished_dir():
    d = Path("/x/polished")
    assert (
        build_polished_path(
            d, date="20260601", source_name="Problem", mode_name="standard_longform"
        ).name
        == "20260601_Problem_standard_longform_polished.mp4"
    )
    assert (
        build_polished_path(
            d, date="20260601", source_name="Problem", mode_name="cinematic_longform"
        ).name
        == "20260601_Problem_cinematic_longform_polished.mp4"
    )


@pytest.fixture
def stub_steps(monkeypatch):
    """Stub the handoff network steps so polish_video orchestration is tested in isolation."""
    calls: list[str] = []
    monkeypatch.setattr(
        handoff, "upload_to_descript", lambda *a, **k: (calls.append("upload"), "PROJ-9")[1]
    )
    monkeypatch.setattr(
        handoff, "import_media", lambda *a, **k: (calls.append("import"), "mediakey")[1]
    )
    monkeypatch.setattr(
        handoff, "_resolve_composition", lambda *a, **k: (calls.append("resolve"), "C-first")[1]
    )
    monkeypatch.setattr(
        handoff, "run_underlord", lambda *a, **k: (calls.append("underlord"), "Jagent")[1]
    )
    monkeypatch.setattr(
        handoff, "wait_for_job", lambda *a, **k: (calls.append("wait"), {"status": "success"})[1]
    )
    monkeypatch.setattr(
        handoff, "publish_project", lambda *a, **k: (calls.append("publish"), "https://dl/x")[1]
    )
    monkeypatch.setattr(
        handoff, "download_polished", lambda url, out, **k: (calls.append("download"), out)[1]
    )
    return calls


def _settings(**kw) -> DescriptSettings:
    base = {"enabled": True, "prompt": "Apply Studio Sound."}
    base.update(kw)
    return DescriptSettings(**base)


def test_full_flow_calls_steps_in_order(stub_steps, tmp_path):
    out = tmp_path / "polished" / "cut_polished.mp4"
    result = polish_video(
        tmp_path / "cut.mp4", _settings(), project_name="Proj", output_path=out, token="tok"
    )
    assert result == out
    assert stub_steps == ["upload", "resolve", "underlord", "wait", "publish", "download"]


def test_empty_prompt_skips_underlord(stub_steps, tmp_path):
    polish_video(
        tmp_path / "cut.mp4",
        _settings(prompt=""),
        project_name="Proj",
        output_path=tmp_path / "out.mp4",
        token="tok",
    )
    assert "underlord" not in stub_steps
    assert "publish" in stub_steps and "download" in stub_steps


def test_cinematic_imports_music_when_music_path_set(stub_steps, tmp_path):
    music = tmp_path / "cinematic_bed.mp3"
    music.write_bytes(b"MP3")
    polish_video(
        tmp_path / "cut.mp4",
        _settings(music_path=music),
        project_name="Proj",
        output_path=tmp_path / "out.mp4",
        token="tok",
    )
    assert stub_steps.index("import") == stub_steps.index("upload") + 1
    assert stub_steps.index("import") < stub_steps.index("underlord")


def test_no_music_import_when_music_path_none(stub_steps, tmp_path):
    polish_video(
        tmp_path / "cut.mp4",
        _settings(),
        project_name="Proj",
        output_path=tmp_path / "out.mp4",
        token="tok",
    )
    assert "import" not in stub_steps


def test_cinematic_missing_music_file_raises_before_upload(stub_steps, tmp_path):
    missing = tmp_path / "nope.mp3"  # never created
    with pytest.raises(RuntimeError):
        polish_video(
            tmp_path / "cut.mp4",
            _settings(music_path=missing),
            project_name="Proj",
            output_path=tmp_path / "out.mp4",
            token="tok",
        )
    assert "upload" not in stub_steps  # fail-loud BEFORE any HTTP / credit spend


class _RecordingConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, message: str) -> None:
        self.messages.append(str(message))


def test_polish_logs_credits(monkeypatch, tmp_path):
    monkeypatch.setattr(handoff, "upload_to_descript", lambda *a, **k: "PROJ-9")
    monkeypatch.setattr(handoff, "_resolve_composition", lambda *a, **k: "C-first")
    monkeypatch.setattr(handoff, "run_underlord", lambda *a, **k: "Jagent")
    monkeypatch.setattr(
        handoff,
        "wait_for_job",
        lambda *a, **k: {"status": "success", "ai_credits_used": 37, "media_seconds_used": 60},
    )
    monkeypatch.setattr(handoff, "publish_project", lambda *a, **k: "https://dl/x")
    monkeypatch.setattr(handoff, "download_polished", lambda url, out, **k: out)
    console = _RecordingConsole()
    polish_video(
        tmp_path / "cut.mp4",
        _settings(),
        project_name="Proj",
        output_path=tmp_path / "out.mp4",
        token="tok",
        console=console,
    )
    assert any("ai_credits_used=37" in m for m in console.messages)


# --------------------------------------------------------------------------- #
# K. dispatch_polish (naming + enablement) and CLI wiring
# --------------------------------------------------------------------------- #


def _mode(mode_name: str, base_dir: str, **descript) -> ModeConfig:
    ds = {"enabled": True, "prompt": "Apply Studio Sound.", **descript}
    return ModeConfig.model_validate(
        {
            "mode_name": mode_name,
            "transcription": {"enabled": False},
            "scoring_weights": {"a": 1.0},
            "pacing": {},
            "rendering": {},
            "output": {"base_dir": base_dir},
            "clip_density": "low",
            "descript": ds,
        }
    )


@pytest.fixture
def capture_polish(monkeypatch):
    """Record polish_video calls made by dispatch_polish; stub token load."""
    calls: list[dict] = []
    monkeypatch.setattr(handoff, "load_descript_token", lambda: "tok")
    monkeypatch.setattr(
        handoff,
        "polish_video",
        lambda video, settings, *, project_name, output_path, token, console=None: (
            calls.append({"video": video, "output_path": output_path, "settings": settings}),
            output_path,
        )[1],
    )
    return calls


def test_polish_disabled_mode_skips_handoff(capture_polish):
    mode = _mode("standard_longform", "src/output/longform/standard", enabled=False)
    cut = [Path("src/output/longform/standard/20260601_Problem_standard_longform.mp4")]
    dispatch_polish(mode, Path("Problem.mp4"), cut)
    assert capture_polish == []


def test_polish_longform_polishes_single_cut(capture_polish):
    mode = _mode("standard_longform", "src/output/longform/standard")
    cut = [Path("src/output/longform/standard/20260601_Problem_standard_longform.mp4")]
    dispatch_polish(mode, Path("Problem.mp4"), cut)
    assert len(capture_polish) == 1
    assert (
        capture_polish[0]["output_path"].name == "20260601_Problem_standard_longform_polished.mp4"
    )
    assert capture_polish[0]["settings"].publish_resolution == "1080p"


def test_polish_on_shortform_raises_clear_error():
    # Phase 9: --polish is longform-only; shortform clips inherit polish from source longform.
    runner = CliRunner()
    result = runner.invoke(cli.app, ["process", "Problem.mp4", "--mode", "shortform", "--polish"])
    assert result.exit_code == 1
    assert "shortform" in result.output
    assert "not supported" in result.output


# ---- CLI-level wiring (process command) ---- #


@pytest.fixture
def cli_pipeline(monkeypatch):
    """Stub the heavy pipeline so the --polish CLI wiring can be exercised."""

    class _WS:
        root = Path("workspace/run_x")
        analysis_dir = Path("workspace/run_x/analysis")
        transcript_path = Path("workspace/run_x/transcript.json")
        moments_path = Path("workspace/run_x/moments.json")

    class _Outputs:
        from_cache: ClassVar[set[str]] = set()
        transcribed = False
        transcript_path = None

    class _Moment:
        start_sec = 1.0
        end_sec = 2.0
        composite_score = 0.9

    class _Moments:
        moments: ClassVar[list[Any]] = [_Moment()]
        moment_count = 1
        below_threshold_best_candidates: ClassVar[list[Any]] = []
        score_threshold = 0.55

    @contextmanager
    def _noop_progress(*a, **k):
        yield lambda *a, **k: None

    monkeypatch.setattr(cli, "ingest", lambda *a, **k: _WS())
    monkeypatch.setattr(cli, "run_analysis", lambda *a, **k: _Outputs())
    monkeypatch.setattr(cli, "run_intelligence", lambda *a, **k: _Moments())
    monkeypatch.setattr(cli, "stage_progress", _noop_progress)
    monkeypatch.setattr(cli, "clip_progress", lambda *a, **k: _noop_progress())
    monkeypatch.setattr(
        cli,
        "run_render",
        lambda *a, **k: [Path("src/output/shorts/zoom_crop/20260601_Problem_clip_01.mp4")],
    )
    monkeypatch.setattr(
        cli,
        "run_longform_render",
        lambda *a, **k: [
            Path("src/output/longform/standard/20260601_Problem_standard_longform.mp4")
        ],
    )

    dispatched: list[dict] = []
    state = {"awake": False, "while_awake": None}

    @contextmanager
    def _track_awake():
        state["awake"] = True
        try:
            yield
        finally:
            state["awake"] = False

    def _dispatch(mode_cfg, source, clips, *, console=None):
        dispatched.append({"mode": mode_cfg.mode_name, "while_awake": state["awake"]})
        return []

    monkeypatch.setattr(cli, "keep_awake", _track_awake)
    monkeypatch.setattr(cli, "dispatch_polish", _dispatch)
    return dispatched


def test_no_polish_flag_runs_no_handoff(cli_pipeline):
    runner = CliRunner()
    result = runner.invoke(cli.app, ["process", "Problem.mp4", "--mode", "shortform"])
    assert result.exit_code == 0
    assert cli_pipeline == []


def test_polish_runs_inside_keep_awake(cli_pipeline):
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["process", "Problem.mp4", "--mode", "standard_longform", "--polish"]
    )
    assert result.exit_code == 0
    assert len(cli_pipeline) == 1
    assert cli_pipeline[0]["mode"] == "standard_longform"
    assert cli_pipeline[0]["while_awake"] is True
