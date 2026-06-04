"""Descript handoff (Phase 9): upload -> one combined Underlord prompt -> publish -> download.

Single file in, single file out. Synchronous and fail-loud: any failed step raises and
leaves the Descript project intact for manual review. Calls the Descript REST API directly
via ``requests`` (no MCP/SDK exists); endpoints/behaviour per docs/descript_api_research.md.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from ..config import PROJECT_ROOT, DescriptSettings, ModeConfig
from ..utils.fileio import ensure_dir
from ..utils.logging import get_logger
from .auth import load_descript_token

__all__ = [
    "DescriptError",
    "RateLimitError",
    "build_polished_path",
    "dispatch_polish",
    "download_polished",
    "import_media",
    "polish_video",
    "publish_project",
    "run_underlord",
    "upload_to_descript",
    "wait_for_job",
]

_log = get_logger(__name__)

BASE_URL = "https://descriptapi.com/v1"
MAX_RATE_LIMIT_RETRIES = 3
_DOWNLOAD_CHUNK = 1 << 20  # 1 MiB

# media-key suffix -> Descript import content_type
_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}


class DescriptError(RuntimeError):
    """A Descript API call failed. Carries the HTTP status code when known."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(DescriptError):
    """A 429 that persisted past the retry budget."""


def _content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _error_for(code: int, path: str, resp: Any) -> DescriptError:
    body = getattr(resp, "text", "") or ""
    if code == 401:
        return DescriptError(
            f"Descript auth failed (401): token invalid/expired; check DESCRIPT_API_KEY. {body}",
            status_code=401,
        )
    if code == 402:
        pool = (
            "out of media minutes (import)"
            if "/jobs/import" in path
            else "out of AI credits (agent)"
            if "/jobs/agent" in path
            else "payment required"
        )
        return DescriptError(f"Descript 402: {pool}. {body}", status_code=402)
    if code == 403:
        detail = "agent usage disabled for this drive" if "/jobs/agent" in path else "forbidden"
        return DescriptError(f"Descript 403: {detail}. {body}", status_code=403)
    if code == 404:
        return DescriptError(f"Descript 404: not found: {path}. {body}", status_code=404)
    return DescriptError(f"Descript error {code}: {path}. {body}", status_code=code)


def _api(
    method: str,
    path: str,
    *,
    token: str,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call a descriptapi.com endpoint; map errors to DescriptError; honour 429 Retry-After (<=3)."""
    url = f"{BASE_URL}{path}"
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        resp = requests.request(method, url, headers=_auth_headers(token), json=json, params=params)
        code = resp.status_code
        if code == 429:
            if attempt >= MAX_RATE_LIMIT_RETRIES:
                raise RateLimitError(
                    f"Descript rate limit (429) persisted after {MAX_RATE_LIMIT_RETRIES} retries: {path}",
                    status_code=429,
                )
            time.sleep(float(resp.headers.get("Retry-After", "1")))
            continue
        if code < 400:
            payload: dict[str, Any] = resp.json()
            return payload
        raise _error_for(code, path, resp)
    raise RateLimitError(f"Descript rate limit (429): {path}", status_code=429)  # pragma: no cover


def _import_media(
    *,
    token: str,
    media_path: Path,
    project_name: str | None = None,
    project_id: str | None = None,
    polling_interval_sec: int = 10,
    timeout_sec: int = 900,
) -> tuple[str, str]:
    """Shared 3-step signed-URL import (POST spec -> PUT bytes -> wait). Returns (project_id, key)."""
    media_path = Path(media_path)
    key = media_path.name
    body: dict[str, Any] = {
        "add_media": {
            key: {
                "content_type": _content_type(media_path),
                "file_size": media_path.stat().st_size,
                "language": "en",
            }
        }
    }
    if project_name is not None:
        body["project_name"] = project_name
        # Place the clip ON a composition timeline. WITHOUT add_compositions Descript creates an
        # EMPTY composition (duration 0); Underlord then has nothing to edit and publish fails
        # "Job failed unexpectedly" — confirmed live 2026-06-01 (agent_response: "composition has no
        # media content"). Research §2.5 L291 / §2.11 L336.
        body["add_compositions"] = [{"name": project_name, "clips": [{"media": key}]}]
    if project_id is not None:
        body["project_id"] = project_id
    resp = _api("POST", "/jobs/import/project_media", token=token, json=body)
    upload_url = resp["upload_urls"][key]["upload_url"]
    put = requests.request(
        "PUT",
        upload_url,
        headers={"Content-Type": "application/octet-stream"},
        data=media_path.read_bytes(),
    )
    if put.status_code >= 400:
        raise DescriptError(
            f"Descript upload PUT failed ({put.status_code}) for {key}", status_code=put.status_code
        )
    wait_for_job(
        resp["job_id"],
        token=token,
        timeout_sec=timeout_sec,
        polling_interval_sec=polling_interval_sec,
    )
    resolved_id = resp.get("project_id") or project_id
    if resolved_id is None:
        raise DescriptError("Descript import did not return a project_id")
    return resolved_id, key


def upload_to_descript(
    video_path: Path,
    project_name: str,
    *,
    token: str,
    polling_interval_sec: int = 10,
    timeout_sec: int = 900,
) -> str:
    """Import a single video into a NEW Descript project; return its ``project_id``."""
    project_id, _ = _import_media(
        token=token,
        media_path=video_path,
        project_name=project_name,
        polling_interval_sec=polling_interval_sec,
        timeout_sec=timeout_sec,
    )
    return project_id


def import_media(
    project_id: str,
    media_path: Path,
    *,
    token: str,
    polling_interval_sec: int = 10,
    timeout_sec: int = 900,
) -> str:
    """Add a media file (e.g. the cinematic music bed) to an EXISTING project; return its key."""
    _, key = _import_media(
        token=token,
        media_path=media_path,
        project_id=project_id,
        polling_interval_sec=polling_interval_sec,
        timeout_sec=timeout_sec,
    )
    return key


def _resolve_composition(project_id: str, *, token: str) -> str:
    """Return the project's composition id (publish requires it)."""
    data = _api("GET", f"/projects/{project_id}", token=token)
    comps = data.get("compositions") or []
    if not comps:
        raise DescriptError(f"project {project_id} has no compositions; cannot publish")
    # TODO(live-test): a single-video import is expected to yield exactly ONE composition.
    # Auto-creation on import is Not documented (research L308); §3.1 confirms compositions are
    # read back from GET /projects/{id}. We take the first; if a project ever exposes multiple,
    # this may need disambiguation — verify against a real run.
    comp_id: str = comps[0]["id"]
    return comp_id


def run_underlord(project_id: str, prompt: str, *, token: str) -> str:
    """Submit ONE combined Underlord agent prompt; return the job id."""
    resp = _api(
        "POST", "/jobs/agent", token=token, json={"project_id": project_id, "prompt": prompt}
    )
    job_id: str = resp["job_id"]
    return job_id


def wait_for_job(
    job_id: str, *, token: str, timeout_sec: int = 900, polling_interval_sec: int = 10
) -> dict[str, Any]:
    """Poll GET /jobs/{id} until terminal. Return result on success; raise on failure/timeout.

    Two-level model: ``job_state == 'stopped'`` is terminal but NOT necessarily successful —
    success requires the nested ``result.status == 'success'``.
    """
    deadline = time.monotonic() + timeout_sec
    while True:
        data = _api("GET", f"/jobs/{job_id}", token=token)
        if data.get("job_state") == "stopped":
            result: dict[str, Any] = data.get("result") or {}
            if result.get("status") == "success":
                return result
            raise DescriptError(
                f"Descript job {job_id} finished with status {result.get('status')!r}: {result}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Descript job {job_id} did not finish within {timeout_sec}s")
        time.sleep(polling_interval_sec)


def publish_project(
    project_id: str,
    composition_id: str,
    resolution: str = "1080p",
    *,
    token: str,
    polling_interval_sec: int = 10,
    timeout_sec: int = 900,
) -> str:
    """Publish the project and return the signed ``download_url`` once the job finishes."""
    body = {
        "project_id": project_id,
        "composition_id": composition_id,
        "media_type": "Video",
        "resolution": resolution,
    }
    try:
        resp = _api("POST", "/jobs/publish", token=token, json=body)
    except DescriptError as exc:
        if exc.status_code in (400, 403):
            raise DescriptError(
                f"Descript publish rejected (resolution {resolution!r} may exceed your plan cap): {exc}",
                status_code=exc.status_code,
            ) from exc
        raise
    result = wait_for_job(
        resp["job_id"],
        token=token,
        timeout_sec=timeout_sec,
        polling_interval_sec=polling_interval_sec,
    )
    url: str | None = result.get("download_url")
    if not url:
        raise DescriptError("Descript publish completed but returned no download_url")
    return url


def download_polished(download_url: str, output_path: Path) -> Path:
    """Stream the published video from its signed URL to ``output_path``.

    The signed ``download_url`` carries its own auth in the URL signature (research §5.1),
    so — like the upload signed-URL leg (§2.4) — no Bearer header is sent here.
    """
    output_path = Path(output_path)
    resp = requests.request("GET", download_url, stream=True)
    if resp.status_code == 403:
        raise DescriptError(
            "Descript download URL expired or forbidden (403); re-publish for a fresh signed URL",
            status_code=403,
        )
    if resp.status_code >= 400:
        raise DescriptError(
            f"Descript download failed ({resp.status_code})", status_code=resp.status_code
        )
    ensure_dir(output_path.parent)
    with output_path.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=_DOWNLOAD_CHUNK):
            fh.write(chunk)
    return output_path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _say(console: Any, message: str) -> None:
    if console is not None:
        console.print(message)


def _resolve_music_path(settings: DescriptSettings) -> Path | None:
    if settings.music_path is None:
        return None
    p = Path(settings.music_path)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def polish_video(
    video_path: Path,
    settings: DescriptSettings,
    *,
    project_name: str,
    output_path: Path,
    token: str,
    console: Any = None,
) -> Path:
    """Run the full handoff for one rendered file; return the downloaded polished path."""
    video_path, output_path = Path(video_path), Path(output_path)
    music_path = _resolve_music_path(settings)
    if music_path is not None and not music_path.exists():
        # Fail loud BEFORE any HTTP — no wasted media-minutes / AI-credits.
        raise DescriptError(f"descript.music_path does not exist: {music_path}")

    _say(console, f"  [descript] uploading {video_path.name}")
    project_id = upload_to_descript(
        video_path,
        project_name,
        token=token,
        polling_interval_sec=settings.polling_interval_sec,
        timeout_sec=settings.timeout_sec,
    )
    if music_path is not None:
        _say(console, f"  [descript] importing music {music_path.name}")
        import_media(
            project_id,
            music_path,
            token=token,
            polling_interval_sec=settings.polling_interval_sec,
            timeout_sec=settings.timeout_sec,
        )
    composition_id = _resolve_composition(project_id, token=token)
    if settings.prompt:
        _say(console, "  [descript] running Underlord (one combined prompt)")
        job_id = run_underlord(project_id, settings.prompt, token=token)
        agent_result = wait_for_job(
            job_id,
            token=token,
            timeout_sec=settings.timeout_sec,
            polling_interval_sec=settings.polling_interval_sec,
        )
        _say(
            console,
            f"  [descript] Underlord done — ai_credits_used="
            f"{agent_result.get('ai_credits_used')}, media_seconds_used="
            f"{agent_result.get('media_seconds_used')}",
        )
    _say(console, "  [descript] publishing")
    download_url = publish_project(
        project_id,
        composition_id,
        settings.publish_resolution,
        token=token,
        polling_interval_sec=settings.polling_interval_sec,
        timeout_sec=settings.timeout_sec,
    )
    _say(console, f"  [descript] downloading -> {output_path.name}")
    return download_polished(download_url, output_path)


def build_polished_path(polished_dir: Path, *, date: str, source_name: str, mode_name: str) -> Path:
    """Polished output path for the single longform cut: ``{date}_{src}_{mode}_polished.mp4``."""
    return Path(polished_dir) / f"{date}_{source_name}_{mode_name}_polished.mp4"


def dispatch_polish(
    mode_cfg: ModeConfig,
    source: Path,
    rendered_clips: list[Path],
    *,
    console: Any = None,
) -> list[Path]:
    """Run the Descript handoff for a mode's rendered longform cut(s). Returns polished paths.

    Polish is longform-only (shortform is rejected at the CLI — its clips are cut FROM polished
    longform and inherit polish). Single file in -> single polished file out; no clip selection.
    """
    settings = mode_cfg.descript
    if not settings.enabled:
        _say(console, f"  [descript] disabled for {mode_cfg.mode_name}; --polish skipped")
        return []
    token = load_descript_token()
    base_dir = str(mode_cfg.model_dump().get("output", {}).get("base_dir", "src/output"))
    polished_dir = PROJECT_ROOT / base_dir / "polished"
    results: list[Path] = []
    for clip in (Path(c) for c in rendered_clips):
        out_path = build_polished_path(
            polished_dir,
            date=clip.name.split("_", 1)[0],
            source_name=source.stem,
            mode_name=mode_cfg.mode_name,
        )
        results.append(
            polish_video(
                clip,
                settings,
                project_name=out_path.stem,
                output_path=out_path,
                token=token,
                console=console,
            )
        )
    return results
