# Descript API — Extensive Research

> **Scope:** Exhaustive, citation-backed reference for the Descript API as documented at `docs.descriptapi.com` (+ help center + labeled secondary sources). Built for the Cinematic Media Engine's Descript-prep handoff. Research date: **2026-05-31**. API status: **public beta**.

**Table of contents**

- [Executive Summary](#executive-summary)
- [Section 1: Agent (Underlord) Capabilities](#section-1-agent-underlord-capabilities)
- [Section 2: Import Capabilities](#section-2-import-capabilities)
- [Section 3: Compositions](#section-3-compositions)
- [Section 4: Editing Operations (Beyond the Agent)](#section-4-editing-operations-beyond-the-agent)
- [Section 5: Publish and Export](#section-5-publish-and-export)
- [Section 6: Partner APIs](#section-6-partner-apis)
- [Section 7: Webhooks and Async Patterns](#section-7-webhooks-and-async-patterns)
- [Section 8: Authentication and Rate Limits](#section-8-authentication-and-rate-limits)
- [Section 9: Pricing and Credits](#section-9-pricing-and-credits)
- [Section 10: CLI (descript-api / @descript/platform-cli)](#section-10-cli-descript-api--descriptplatform-cli)
- [Section 11: Specific Use-Case Evaluation](#section-11-specific-use-case-evaluation)
- [Section 12: Agent Prompt Examples](#section-12-agent-prompt-examples)
- [Section 13: Integration Architecture Patterns](#section-13-integration-architecture-patterns)
- [Section 14: Limitations and Gotchas](#section-14-limitations-and-gotchas)
- [Section 15: Concrete Examples of Agent Power](#section-15-concrete-examples-of-agent-power)
- [Architecture Recommendation](#architecture-recommendation)
- [Cost Analysis](#cost-analysis)
- [Open Questions](#open-questions)
- [Appendix: Sources & Verification Notes](#appendix-sources--verification-notes)

---

## Executive Summary

The **Descript API** (public beta; base URL `https://descriptapi.com/v1`, Bearer-token auth scoped to a Drive) is a small, asynchronous, **job-oriented** REST API built around three verbs — **import** media, run the **Underlord AI agent**, and **publish** — plus read/list/cancel endpoints for jobs and projects (source: https://docs.descriptapi.com/). It is **not** a timeline-editing API: there is no documented low-level operation to trim a clip, set a transition, or place a lower-third by coordinates. Instead, **all editing is expressed as natural-language prompts** to one endpoint, `POST /jobs/agent` — *"If you can prompt it with Underlord in Descript, you can automate it through the API"* (source: https://www.descript.com/api). The finished **rendered video** leaves the system through `POST /jobs/publish`, which returns a hosted `share_url` plus a **time-limited signed `download_url`** — there is **no separate render-the-video-to-a-file endpoint** outside publish. The API is **not** publish-only for output, though: two **direct, non-agent export endpoints** return a file straight in the response body — **`POST /export/transcript`** (plain text / Markdown / VTT) and **`POST /export/timeline`** (FCPXML / Premiere Pro XML / EDL / AAF for Premiere, Final Cut, Resolve) (source: https://docs.descriptapi.com/openapi-experimental.html). These are *export*, not *editing*.

**Key capabilities relevant to a local-preprocess → Descript-polish engine.** The agent reliably performs exactly the polish steps a *prep* engine should omit — captions, Studio Sound, filler-word removal, highlight/trailer cuts, B-roll (generated or stock-library), layouts, title cards, and content-level pacing decisions — and it **reports what it did** in the job result (`agent_response`, `project_changed`, `media_seconds_used`, `ai_credits_used`) (source: https://docs.descriptapi.com/openapi-experimental.html). Multiple imported files can be assembled into a single composition in one import call. Jobs are asynchronous with optional `callback_url` webhooks on import/agent/publish.

**Major limitations to know up front:**
1. **No color grading / LUT support** — an open community feature request; users export to DaVinci Resolve instead. Keep grading in-engine. [secondary: descript.canny.io]
2. **Thumbnails are an app/Underlord capability, not a documented API one.** In-app, Underlord generates styled thumbnail *text* and AI *images*, and you can set a project (share-page) thumbnail. But the **API** has no thumbnail operation and **no documented way to retrieve a finished thumbnail image** — so thumbnails stay engine-side for automation (see §1.3).
3. **No transcript or metadata sidecar import** — the engine's `transcript.json` and `thumbnail_text_suggestions` **cannot flow into Descript's edit decisions** via the API; import auto-transcribes instead.
4. **The agent is one-shot** — Descript explicitly recommends framing edits as a single prompt; no conversational follow-up.
5. **No direct local export, no documented codec/container selector (MP4 vs MOV), no per-clip timeline control.**
6. **Per-credit / per-operation dollar pricing is not in the API docs** — only a media-minutes + AI-credits consumption model; dollar figures below are secondary.

**Caveat on sourcing.** This reference was built from the **official Descript API docs** (`docs.descriptapi.com`, including its `openapi-experimental.html` spec) + **`help.descript.com`** + clearly-labeled **secondary** sources (descript.com/pricing, news, third-party reviews). **Context7 / package registries do NOT index this API** — their only `descript` entry is an unrelated open-source HTTP-request library — so this document was assembled by direct documentation fetches, **not** a registry index. Where neither primary nor secondary sources state a fact, the text says **"Not documented"** rather than guessing. An automated adversarial-verification agent pass was interrupted mid-run; verification was completed by manual review of all 148 extracted claims (105 primary / 39 secondary / 4 inferred) — see the Appendix.

## Section 1: Agent (Underlord) Capabilities

The Descript API exposes the Underlord AI co-editor through a single endpoint: **`POST /jobs/agent`** (base URL `https://descriptapi.com/v1`, Bearer-token auth scoped to a Drive) (source: https://docs.descriptapi.com/). The agent takes a single free-text `prompt` and performs whatever editing operations that prompt implies, asynchronously, returning a `job_id` you poll via `GET /jobs/{job_id}` (source: https://docs.descriptapi.com/openapi-experimental.html).

The foundational scope statement is on the product/API marketing page: **“If you can prompt it with Underlord in Descript, you can automate it through the API.”** (source: https://www.descript.com/api). Therefore the agent’s capability surface = Underlord’s capability surface, which is the basis for the operation list below.

### 1.1 Edit operations the agent supports via natural language

The API page states Underlord/the agent can “Import, transcribe, remove filler words, add Studio Sound, cut clips, generate captions” and perform “whatever you’ve told it to do” (source: https://www.descript.com/api). Documented Underlord operations (which the API can therefore drive) include:

| Operation | Supported? | Source / notes |
|---|---|---|
| **Studio Sound** (audio enhancement) | Yes | Named feature; example prompt “add studio sound to every clip”; agent_response confirms “I’ve applied Studio Sound” (source: https://docs.descriptapi.com/). Volume/level control documented: “apply Studio sound at about 55%” (source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer). AI audio effect that reduces background noise, echo, and distractions for clearer, more professional voice; **applied at the FILE level — once enabled it affects every instance of that file across the project** (not per-clip); requires an internet connection [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10327603613837-Studio-Sound) |
| **Captions / subtitles** | Yes | Example prompt “Add studio sound and captions”; “added classic karaoke-style captions” (source: https://docs.descriptapi.com/). Workflow prompt “add captions to the entire video” (source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer) |
| **Filler-word removal** | Yes | “remove all filler words from the transcript” (source: https://docs.descriptapi.com/); “Cut filler words” (source: https://www.descript.com/underlord) |
| **Highlight reels** | Yes | “create a 30-second highlight reel” (source: https://docs.descriptapi.com/); “make the trailer for this webinar recording” (source: https://www.descript.com/underlord) |
| **Cuts / trimming by timecode or topic** | Yes | “remove the section from 1:30 to 2:15”; “Edit this down to 2 minutes—focusing on when I talked about Freud” (source: https://www.descript.com/underlord) |
| **Remove retakes / bad takes** | Yes | “Remove Retakes” / “remove retakes—use the last complete take” (source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer) |
| **Transitions / camera moves / zooms** | Partial | “Hide my jumpcuts by adding zooms” (source: https://www.descript.com/underlord). Discrete “transition” primitives are Not documented as a named agent capability |
| **B-roll** | Yes (generated + stock) | “Generate images/B-roll”, “Find scenes that would benefit from B-roll. Use generated images…” (source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer). “Unlimited access to royalty-free stock media library” [secondary: descript.com/underlord] (source: https://www.descript.com/underlord) |
| **Background music** | Yes (via prompt) | “create a 30-second video about cooking tips with background music” (source: https://docs.descriptapi.com/). Source is Descript’s royalty-free stock media library [secondary: descript.com/underlord] (source: https://www.descript.com/underlord) |
| **Layouts / camera layouts (“multicam”)** | Yes | “Add camera layouts every time I reference…”; “apply the Berlin Carrot layout to the entire video”; “Automatic Multicam” (source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer) |
| **Title cards / chapter markers / show notes** | Yes | “add a title card…”, “create chapter markers” (source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer) |
| **Eye Contact correction, Green Screen, Regenerate Speech/Video** | Yes | Named Underlord features (source: https://www.descript.com/underlord) |
| **Script writing + full video generation** | Yes | “Write a script about how to make great coffee”; “create a 30-second video about cooking tips” (source: https://docs.descriptapi.com/) |
| **Translation / dubbing** | Yes | Listed agent capability (source: https://www.descript.com/api) |
| **Color grading / LUTs** | **No** | Not a documented Underlord/agent capability; community feature request open, users export to DaVinci Resolve [secondary: descript.canny.io] (source: https://descript.canny.io/feature-requests/p/applying-custom-luts) |
| **AI thumbnail (image) generation via agent** | Not documented | See §1.3 |

### 1.2 Pacing / cut-selection decisions

The agent makes content-level pacing and selection decisions. Prompts such as “make this a fast-paced highlight reel for TikTok… less than 60 seconds,” “Edit this down to 2 minutes—focusing on when I talked about Freud,” and “Turn this hour-long webinar… into a 5-minute YouTube video that just hits on the most compelling points” show the agent choosing what to keep/cut and overall pace from longer source footage (source: https://www.descript.com/underlord; source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer). Frame-accurate “slow down here” speed-ramping as a named primitive is **Not documented**.

### 1.3 AI thumbnail generation

**In the Descript app / Underlord — yes, with nuance:**
- **Thumbnail *text*** generation is a documented Underlord tool (“YouTube Thumbnail Text”): it writes and *styles* the text (fonts, color, animation, bold/italic/underline), but the output is **copyable text you paste elsewhere**, not a baked-in finished thumbnail image [secondary: descript.com/tools] (source: https://www.descript.com/tools/youtube-thumbnail-text-generator).
- **AI *image* generation** via Underlord is documented — “generate an image right inside Descript… from a simple text prompt” (used for B-roll or as a visual) (source: https://help.descript.com/hc/en-us/articles/17045119670029-Generate-AI-images-using-Underlord).
- A project **thumbnail** in Descript is the **preview image for a web link / share page**; you can set it from any video frame, an uploaded image, or a generated image [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/33721214075917-Set-and-Customize-Thumbnails). So in-app you *can* assemble an AI-generated visual + styled thumbnail text and set it as the project thumbnail.

**Via the API — Not documented:**
- There is no thumbnail-specific field in the `POST /jobs/agent` schema; a thumbnail would have to be requested through the free-text `prompt` like any other instruction (untested), and **no documented endpoint returns a finished thumbnail image asset** — publish yields the video plus a share-page preview, not a downloadable YouTube thumbnail file (source: https://docs.descriptapi.com/). No thumbnail prompt syntax is published. **For automation, the engine’s own `thumbnail_candidates` + `thumbnail_text_suggestions` remain the reliable path.**

### 1.4 B-roll source: library vs imported

Both. The agent can (a) **generate** B-roll images (“Use generated images in a vibrant, documentary-inspired style…”) and (b) draw from Descript’s **royalty-free stock media library** (source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer; [secondary: descript.com/underlord] source: https://www.descript.com/underlord). It can also use media you imported into the project. Whether the agent auto-sources stock B-roll without explicit instruction is not crisply documented.

### 1.5 Background music source: library vs imported

Descript provides a royalty-free background-music/stock library that the agent can pull from when a prompt requests “background music” [secondary: descript.com/underlord] (source: https://www.descript.com/underlord; source: https://docs.descriptapi.com/). Imported music is also usable. The agent does not generate original AI music (Not documented).

### 1.6 Scene / cut selection from longer source footage

Supported. Prompts like “Copy all the parts where Laura is talking into a new composition,” “Make the trailer for this webinar recording,” and “create a 30-second highlight reel” demonstrate the agent selecting scenes/segments from longer recordings (source: https://www.descript.com/underlord; source: https://docs.descriptapi.com/).

### 1.7 Color grading / LUTs via agent

**Not supported / Not documented.** No color-grade or LUT agent operation is documented; it is an open community feature request, with users noting they must export to external tools [secondary: descript.canny.io] (source: https://descript.canny.io/feature-requests/p/applying-custom-luts).

### 1.8 What the agent does NOT support (documented gaps)

- Color grading / custom LUT application [secondary] (source: https://descript.canny.io/feature-requests/p/applying-custom-luts).
- Named discrete transition library, AI music generation, dedicated AI thumbnail-image endpoint — **Not documented.**
- General constraints are tracked on the API known-issues page (source: https://www.descript.com/api references https://descript.notion.site/descript-apis-known-issues) — page content could not be retrieved (returned empty/blocked), so specific listed limitations are **Not documented** here.

### 1.9 Request / response shape and how success is measured

The API **does** report what the agent did. The job-status `result` object returns a natural-language `agent_response` summary plus `project_changed`, `media_seconds_used`, and `ai_credits_used` (source: https://docs.descriptapi.com/; source: https://docs.descriptapi.com/openapi-experimental.html).

**Request body (`POST /jobs/agent`):**

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | **Yes** | Natural-language instruction for the agent to execute |
| `project_id` | string (uuid) | Conditional | Existing project to edit. Mutually exclusive with `project_name` |
| `project_name` | string | Conditional | Name for a new project. Mutually exclusive with `project_id` |
| `composition_id` | string | Optional | Target composition within the project; accepts full UUID, 5-char short ID, or full Descript URL. Only valid when `project_id` is also provided |
| `model` | string | Optional | AI model to use; “Defaults to the default model.” No specific values enumerated |
| `team_access` | enum `edit\|comment\|view\|none` | Optional | Team access when creating a new project; defaults to `none` |
| `callback_url` | string (uri) | Optional | Webhook POSTed when the job completes or fails |

(source: https://docs.descriptapi.com/; source: https://docs.descriptapi.com/openapi-experimental.html)

**201 response (job created) (copied from docs):**

```json
{
  "job_id": "6dc3f30a-58c2-4174-96a6-dc18cf3c7776",
  "drive_id": "c9c5c47e-158a-49f7-846b-4f6ee2a229a2",
  "project_id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "project_url": "https://web.descript.com/9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb"
}
```
(source: https://docs.descriptapi.com/openapi-experimental.html)

**Completed job (`GET /jobs/{job_id}`) result fields:** `status` (e.g. `"success"`), `agent_response` (string summary), `project_changed` (bool), `media_seconds_used` (number), `ai_credits_used` (number) (source: https://docs.descriptapi.com/openapi-experimental.html).

**Async behavior:** “Agent edits run in the background and return a `job_id`. Monitor progress via the GET /jobs/{job_id} endpoint.” If `callback_url` is set, Descript POSTs job status there on completion/failure (source: https://docs.descriptapi.com/).

**Error codes:** 400 invalid input (incl. empty/invalid prompt); 401 missing/invalid token; 402 insufficient AI credits; 403 drive admin disabled agent usage; 404 project/composition not found; 429 rate-limited (`Retry-After`) (source: https://docs.descriptapi.com/openapi-experimental.html).

## Section 2: Import Capabilities

All import in the Descript API flows through a single asynchronous endpoint: **`POST /jobs/import/project_media`** (base URL `https://descriptapi.com/v1`, Bearer-token auth scoped to a Drive) (source: https://docs.descriptapi.com/). One request can create a project (or target an existing one), pull in one or more media files (by URL or by direct signed-URL upload), optionally arrange them into compositions, and trigger transcription — all in a single call (source: https://docs.descriptapi.com/).

### 2.1 Supported formats, codecs, and containers

The API reference does not publish a standalone "supported formats" table for the import endpoint; the only inline format list in the docs appears in the **Edit in Descript** partner section, and Descript's general app behavior is documented in the help center. Treat the following as the documented set:

| Category | Documented support | Source |
|---|---|---|
| Audio | WAV, FLAC, AAC, MP3 | docs.descriptapi.com (Edit in Descript section) |
| Video codecs | h264, HEVC | docs.descriptapi.com (Edit in Descript section) |
| Video containers | MOV, MP4 | docs.descriptapi.com (Edit in Descript section) |

Verbatim from the docs' Edit in Descript section: **Audio: "WAV, FLAC, AAC, MP3"**; **Video: "h264, HEVC (container: MOV, MP4)"** (source: https://docs.descriptapi.com/).

Descript's broader app-level format support (help center) is wider than the partner list and includes additional containers/codecs. Reported support: MKV/Matroska (Descript supports the codecs inside MKV that it already handles in other containers), WebM (VP8/VP9 video, Opus audio), and AVIF images [secondary: search summary of help.descript.com "Supported file types" article, https://help.descript.com/hc/en-us/articles/10164098416909-Supported-file-types]. Cleanvoice's third-party review lists "WAV, FLAC, MP3, MOV, and MP4" as accepted formats [secondary: https://cleanvoice.ai/blog/descript-api-review/].

**Codec-in-container caveat:** A "file not supported" error on an otherwise-supported extension is typically caused by an unsupported codec inside the container — not every codec works in every container [secondary: search summary of https://help.descript.com/hc/en-us/articles/10164098416909-Supported-file-types].

**Multi-channel audio:** Surround/multi-channel audio (e.g., 5.1) is accepted, but files with three or more audio channels are automatically downmixed to stereo on import [secondary: search summary of https://help.descript.com/hc/en-us/articles/10164098416909-Supported-file-types].

**Resolution limits:** Not documented.

### 2.2 Two import mechanisms

Each entry in the `add_media` map uses **one of two mutually exclusive shapes**:

1. **URL import** — supply a `url` Descript's servers can fetch.
2. **Direct upload** — supply `content_type` + `file_size`; the API returns a signed PUT URL you upload bytes to.

#### add_media item schema

| Field | Type | Used by | Notes |
|---|---|---|---|
| `url` | string (URI) | URL import | Must be reachable by Descript servers; must support HTTP Range requests (source: https://docs.descriptapi.com/) |
| `content_type` | string (MIME) | Direct upload | e.g. `video/mp4`, `audio/wav` (source: https://docs.descriptapi.com/) |
| `file_size` | integer (bytes) | Direct upload | Size of the file being uploaded (source: https://docs.descriptapi.com/) |
| `language` | string (optional) | both | ISO 639-1 code; auto-detected if omitted (source: https://docs.descriptapi.com/openapi-experimental.html) |

The map **key** is a display name / reference ID that may include a folder path using `/` (e.g. `"Misc/intro.mp4"`); that same key is what `add_compositions` clips reference (source: https://docs.descriptapi.com/).

### 2.3 URL import flow (recommended for automation)

URL import is the simplest path to automate: one POST, then poll. The canonical automation pattern is "file lands in an S3 bucket → API call pulls it into Descript" [secondary: https://www.vp-land.com/p/descript-adds-an-api-and-agentic-automation-to-its-text-based-video-editor].

Requirements: "URLs must be accessible by Descript servers" and "URLs must support HTTP Range requests"; pre-signed URLs are recommended to remain valid for roughly **12–48 hours** (source: https://docs.descriptapi.com/).

```json
POST https://descriptapi.com/v1/jobs/import/project_media
Authorization: Bearer <DRIVE_SCOPED_TOKEN>
Content-Type: application/json

{
  "project_name": "Marketing Video",
  "team_access": "edit",
  "folder_name": "Clients/Acme/Videos",
  "add_media": {
    "demo.mp4": { "url": "https://test-files.descriptapi.com/demo-video.mp4" },
    "Misc/intro.mp4": { "url": "https://example.com/intro.mp4" }
  },
  "add_compositions": [
    {
      "name": "Demo Video",
      "clips": [
        { "media": "demo.mp4" },
        { "media": "Misc/intro.mp4" }
      ]
    }
  ],
  "callback_url": "https://example.com/webhook"
}
```

Response (`201 Created`) — no `upload_urls` because everything was a URL import:

```json
{
  "job_id": "6dc3f30a-58c2-4174-96a6-dc18cf3c7776",
  "drive_id": "c9c5c47e-158a-49f7-846b-4f6ee2a229a2",
  "project_id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "project_url": "https://web.descript.com/9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb"
}
```
(source: https://docs.descriptapi.com/)

Then poll `GET /jobs/{job_id}` until `job_state: "stopped"` (source: https://docs.descriptapi.com/).

### 2.4 Direct upload flow (3-step signed-URL)

Use direct upload when the source bytes live on a machine that cannot expose a Descript-reachable URL.

**Step 1 — Request signed URLs.** POST with `content_type` + `file_size` instead of `url`:

```json
POST https://descriptapi.com/v1/jobs/import/project_media
Authorization: Bearer <DRIVE_SCOPED_TOKEN>
Content-Type: application/json

{
  "project_name": "Podcast Episode 42",
  "add_media": {
    "recording.mp4": {
      "content_type": "video/mp4",
      "file_size": 52428800,
      "language": "en"
    }
  }
}
```

Response includes an `upload_urls` object keyed by the media reference ID:

```json
{
  "job_id": "project-media-import-a1b2c3d4",
  "drive_id": "c9c5c47e-158a-49f7-846b-4f6ee2a229a2",
  "project_id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "project_url": "https://web.descript.com/9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "upload_urls": {
    "recording.mp4": {
      "upload_url": "https://storage.googleapis.com/bucket/...",
      "asset_id": "d4e5f6a7-...",
      "artifact_id": "a1b2c3d4-..."
    }
  }
}
```
(source: https://docs.descriptapi.com/)

**Step 2 — Upload bytes.** `PUT` the raw file to `upload_url` with header `Content-Type: application/octet-stream`. **The signed URL is valid for 3 hours** (source: https://docs.descriptapi.com/). The same 3-hour expiry is independently confirmed [secondary: https://cleanvoice.ai/blog/descript-api-review/].

```bash
curl -X PUT "https://storage.googleapis.com/bucket/..." \
  -H "Content-Type: application/octet-stream" \
  --data-binary @recording.mp4
```

**Step 3 — Poll.** `GET /jobs/{job_id}` until `job_state: "stopped"`; the import job detects the uploaded bytes automatically and finishes processing (source: https://docs.descriptapi.com/).

**URL import vs direct upload — which for automation?** URL import is the lower-friction automation path: it is a single POST + poll (no separate byte-upload leg), and it fits the documented S3-trigger pattern, provided the source can produce a Descript-reachable, Range-supporting URL with a long-lived signature (12–48h). Direct upload is the fallback when no such URL exists, at the cost of an extra PUT step and a shorter (3h) window. (Inferred from the two documented flows; source: https://docs.descriptapi.com/.)

### 2.5 Multi-source assembly into one project

Yes. `add_media` is a map, so a single request can import several files; `add_compositions[].clips[]` can then reference multiple media keys in sequence to assemble them into one timeline (e.g., `intro.mp4` then `demo.mp4`) (source: https://docs.descriptapi.com/). Additional media can also be added later by POSTing to the same endpoint with the existing `project_id`; filename conflicts within an existing project are prevented (source: https://docs.descriptapi.com/openapi-experimental.html).

### 2.6 Importing a separate transcript file (SRT/VTT/JSON)

**Not documented** as an API feature. The `add_media` schema has no field for attaching a transcript/caption sidecar, and the import endpoint instead **generates** the transcript itself during processing (source: https://docs.descriptapi.com/). Descript supports "use your own transcript" as an **app** feature [secondary: https://help.descript.com/hc/en-us/articles/10249406128781-Importing-an-existing-transcript], but no equivalent is exposed in the documented API request body.

### 2.7 Importing a metadata sidecar (chapters / markers / thumbnail suggestions)

**Not documented.** The `add_media` and `add_compositions` schemas contain no fields for chapters, markers, or thumbnail suggestions on import (source: https://docs.descriptapi.com/). Markers/chapters exist in the Descript app and can be embedded on local *export* (MP4/MP3/AAC) [secondary: search summary of https://help.descript.com/hc/en-us/articles/10164735239693-Markers-and-chapters], but there is no documented import-side sidecar ingestion via the API.

### 2.8 Multitrack sequences

The import schema documents a flat `clips` array (each clip referencing one `media` key) within a composition; it does **not** document per-clip track/layer assignment, multiple parallel tracks, or multitrack media objects at import time (source: https://docs.descriptapi.com/). Multitrack/layered structure is a concept in the Descript app timeline (see Section 3) but is **Not documented** as something you build via the import request. A competitor review notes users report performance issues on "larger/multi-track projects" but does not describe API multitrack support [secondary: https://cleanvoice.ai/blog/descript-api-review/].

### 2.9 What happens automatically on import

- **Transcription:** Yes, automatic. "This step also transcribes and processes the media so that it's ready for you or the agent to edit" (source: https://docs.descriptapi.com/). The optional `language` field guides this; it is auto-detected if omitted (source: https://docs.descriptapi.com/openapi-experimental.html).
- **Composition setup:** The import call can set up a composition in the same step when `add_compositions` is supplied (source: https://docs.descriptapi.com/). Whether a composition is auto-created when `add_compositions` is omitted is **Not documented**.
- **Scene detection:** **Not documented** — no mention of automatic scene detection on import (source: https://docs.descriptapi.com/).
- **Billing side effects:** Importing/processing consumes **media minutes** from the plan; insufficient balance returns HTTP **402 Payment Required** [secondary: search summary of https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta].

### 2.10 File size, duration limits, upload-URL expiry

| Constraint | Value | Source / confidence |
|---|---|---|
| Direct-upload signed URL expiry | **3 hours** | Primary (source: https://docs.descriptapi.com/); also [secondary: https://cleanvoice.ai/blog/descript-api-review/] |
| Recommended URL-import pre-signed validity | **12–48 hours** | Primary (source: https://docs.descriptapi.com/) |
| API import file-size limit | Not documented in the API reference | (The 50 MB / `52428800` value in the docs is an *example* `file_size`, not a cap.) (source: https://docs.descriptapi.com/) |
| API import duration limit | Not documented in the API reference | — |
| Resolution limit | Not documented | — |
| Number of files per request | Not documented (no stated cap on `add_media` size) | (source: https://docs.descriptapi.com/) |

Related app-level numbers (the **Edit in Descript / app import** path, which is distinct from the API import endpoint and may not apply identically): imported files "limited to 1 GB," and any file longer than "15 hours" fails to transcribe automatically [secondary: search summaries of https://help.descript.com/hc/en-us/articles/12942138820621-Import-files-using-Edit-in-Descript-beta and https://help.descript.com/hc/en-us/articles/10164098416909-Supported-file-types]. These are flagged as app-feature limits, not confirmed API import-endpoint limits.

### 2.11 Full endpoint summary — POST /jobs/import/project_media

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `project_id` | UUID | optional | Import into existing project; mutually exclusive with `project_name` |
| `project_name` | string | required if no `project_id` | Name for a new project |
| `team_access` | enum | optional | `edit` \| `comment` \| `view` \| `none` (default `none`) |
| `folder_name` | string | optional | Nested path via `/` (e.g. `Clients/Acme/Videos`) |
| `add_media` | object (map) | required | media-key → import spec (`url` **or** `content_type`+`file_size`; optional `language`) |
| `add_compositions` | array | optional | each: `{ name, clips: [{ media }] }` |
| `callback_url` | URI | optional | webhook fired on job completion |

(source: https://docs.descriptapi.com/ and https://docs.descriptapi.com/openapi-experimental.html)

**Response (201)**: `job_id`, `drive_id`, `project_id`, `project_url`, and `upload_urls` (present only for direct-upload media) (source: https://docs.descriptapi.com/). Errors include 402 (out of media minutes) and 429 (rate limited, with `Retry-After`, `X-RateLimit-Remaining`, `X-RateLimit-Consumed`) (source: https://docs.descriptapi.com/).

## Section 3: Compositions

### 3.1 Is "composition" a first-class API object?

**Partly.** A composition is **not an independently addressable top-level REST resource** — there is **no `GET /compositions`, no `GET /compositions/{id}`, and no `POST /compositions` documented**. Instead, a composition exists **inside a project**:

- It is **created** as a nested element of the import request, via the `add_compositions` array of `POST /jobs/import/project_media` (source: https://docs.descriptapi.com/).
- It is **read back** as the `compositions` array embedded in the `GET /projects/{project_id}` response (source: https://docs.descriptapi.com/).
- It is **targeted for editing** by passing a `composition_id` to `POST /jobs/agent` (source: https://docs.descriptapi.com/).

So the API data model is **Drive → Project → { media_files, compositions }**, where a composition is a sub-object of a project (a timeline). It has its own `id` (and accepts a full UUID, a 5-char short ID, or a full Descript URL when referenced on the agent endpoint), a `name`, a `duration`, and a `media_type` (source: https://docs.descriptapi.com/). It is best understood as a *nested, addressable-by-id timeline within a project*, not a standalone resource you CRUD directly.

In Descript's own terminology, a project is the container and a composition is a timeline/sequence within it [secondary: search summary of https://help.descript.com/hc/en-us/articles/13535123897485-Projects-and-compositions-overview].

### 3.2 How compositions are structured (clips / tracks / layers)

**Via the API**, the only documented structural element of a composition is the **`clips` array**, where each clip references one media key:

```json
{
  "name": "Demo Video",
  "clips": [
    { "media": "demo.mp4" },
    { "media": "Misc/intro.mp4" }
  ]
}
```
(source: https://docs.descriptapi.com/)

The documented clip object exposes **only** the `media` field. **Per-clip `start`/`end` trims, explicit ordering offsets, `track`, and `layer` fields are Not documented** in the clip schema (source: https://docs.descriptapi.com/). Clip ordering is implied by array position; nothing else about within-composition structure is exposed by the API.

The docs explicitly stop short of describing internal track/layer/overlay structure within a composition (source: https://docs.descriptapi.com/).

**In the Descript app** (concept, not API), a composition's timeline does have richer structure — layer lanes for video, audio, images, text, shapes, B-roll and effects; "clips" are segments that exist within audio/video layers; and layers control stacking/visibility/order [secondary: search summaries of https://help.descript.com/hc/en-us/articles/10249275208717-Timeline-overview, https://help.descript.com/hc/en-us/articles/10301481327757-Layer-and-clip-overview, https://help.descript.com/hc/en-us/articles/10248940771981-Add-layers-to-your-composition]. **None of that layer/lane structure is exposed for programmatic construction in the documented API.**

### 3.3 Answering the specific sub-questions

| Question | Answer | Source |
|---|---|---|
| Created programmatically with specific clips? | Yes — `add_compositions[].clips[].media` at import time | Primary (source: https://docs.descriptapi.com/) |
| Specific timing per clip via API? | Not documented (clip object exposes only `media`) | (source: https://docs.descriptapi.com/) |
| Cuts / ordering via API? | Ordering is implied by clip array position; explicit cut/trim params Not documented | (source: https://docs.descriptapi.com/) |
| Layers (lower thirds / titles / overlays) via API? | Not documented at the timeline level — only achievable by prompting the agent (e.g. "add captions", "generate B-roll") | Primary for agent route (source: https://docs.descriptapi.com/) |
| Reference media imported in same project? | Yes — clips reference media keys from the same `add_media` map | Primary (source: https://docs.descriptapi.com/) |
| Multitrack support via API? | Not documented (flat `clips` array only) | (source: https://docs.descriptapi.com/) |

### 3.4 GET /projects

Lists projects in the token's Drive with filtering, sorting, and cursor pagination (source: https://docs.descriptapi.com/). A sibling read-only endpoint, **`GET /projects/folders`**, lists the Drive's folders (source: https://docs.descriptapi.com/openapi-experimental.html).

**Query parameters**

| Param | Type | Values / default |
|---|---|---|
| `name` | string | case-insensitive substring filter |
| `folder_path` | string | e.g. `Clients/Acme/Videos` |
| `created_by` | UUID \| `me` | filter by creator |
| `created_after` / `created_before` | ISO 8601 | datetime bounds |
| `updated_after` / `updated_before` | ISO 8601 | datetime bounds |
| `sort` | enum | `name` \| `created_at` \| `updated_at` \| `last_viewed_at` (default `created_at`) |
| `direction` | enum | `asc` \| `desc` (default `desc`) |
| `cursor` | string | pagination token |
| `limit` | integer | 1–100 (default 20) |

(source: https://docs.descriptapi.com/openapi-experimental.html)

**Response (200)**

```json
{
  "data": [
    {
      "id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
      "name": "Marketing Video",
      "created_at": "2025-11-18T10:30:00Z",
      "updated_at": "2025-11-19T14:00:00Z",
      "folder_path": "Clients/Acme/Videos"
    }
  ],
  "pagination": { "next_cursor": "string" }
}
```
(source: https://docs.descriptapi.com/) Note: the list-item objects do **not** include `media_files` or `compositions`; those appear only in the single-project detail call.

### 3.5 GET /projects/{project_id}

Returns full project detail including embedded media and compositions (source: https://docs.descriptapi.com/).

**Response (200)**

```json
{
  "id": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "name": "string",
  "drive_id": "9058289f-c0b4-40fe-9269-4ada6478fd11",
  "created_at": "2019-08-24T14:15:22Z",
  "updated_at": "2019-08-24T14:15:22Z",
  "folder_path": "Clients/Acme/Videos",
  "media_files": {
    "demo.mp4": { "type": "audio", "duration": 0.1 }
  },
  "compositions": [
    {
      "id": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
      "name": "string",
      "duration": 0.1,
      "media_type": "string"
    }
  ]
}
```
(source: https://docs.descriptapi.com/)

**Field reference**

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | project id |
| `name` | string | project name |
| `drive_id` | UUID | owning Drive |
| `created_at` / `updated_at` | ISO 8601 | timestamps |
| `folder_path` | string | full folder path |
| `media_files` | object (map) | key = display path; value = `{ type: audio\|video, duration: seconds }` |
| `compositions` | array | each = `{ id, name, duration (seconds), media_type }` |

(source: https://docs.descriptapi.com/ and https://docs.descriptapi.com/openapi-experimental.html)

**Key gap:** The composition objects returned here are **summary-level** (`id`, `name`, `duration`, `media_type`). There is **no documented endpoint that returns the internal clip/track/layer breakdown of a composition** — the detailed timeline contents are not retrievable via the API (source: https://docs.descriptapi.com/).

## Section 4: Editing Operations (Beyond the Agent)

### 4.1 Bottom line

**Not documented — editing is agent-mediated.** There is **no low-level / direct timeline-editing API** in the documented Descript API surface. There are no endpoints to trim a clip by timecode, reorder existing clips, add a transition, insert a title / lower third / text overlay, add a music track, or set per-track volume / audio mixing as discrete typed operations. Every post-import edit is expressed as a **natural-language prompt to the Underlord agent** via `POST /jobs/agent` (source: https://docs.descriptapi.com/).

The only structural manipulation available without the agent is **clip-level assembly at import time** — ordering media keys in `add_compositions[].clips[]` (covered in Sections 2–3). That is composition *assembly*, not timeline *editing* of an existing composition (source: https://docs.descriptapi.com/).

A third-party review reinforces the absence of a direct manipulation API, stating developers "can't modify or manipulate the recordings within Descript programmatically" outside the provided mechanisms [secondary: https://cleanvoice.ai/blog/descript-api-review/]. A news summary similarly describes the API as exposing "Underlord actions" and "automated ingest," not a timeline-primitive API [secondary: https://www.vp-land.com/p/descript-adds-an-api-and-agentic-automation-to-its-text-based-video-editor].

### 4.2 The agent edit endpoint — POST /jobs/agent

This is the editing entry point. "You can use the agent edit endpoint to prompt Underlord for edits, just as you would in the app" (source: https://docs.descriptapi.com/).

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `project_id` | UUID | one of `project_id`/`project_name` | edit an existing project |
| `project_name` | string | one of `project_id`/`project_name` | create a new project to operate on |
| `composition_id` | string | optional | target a specific composition; accepts full UUID, 5-char short id, or full Descript URL |
| `prompt` | string | **required** | natural-language instruction |
| `model` | string | optional | AI model selection (default model used if omitted) |
| `team_access` | enum | optional | `edit` \| `comment` \| `view` \| `none` (default `none`) |
| `callback_url` | URI | optional | webhook on completion |

(source: https://docs.descriptapi.com/ and https://docs.descriptapi.com/openapi-experimental.html)

```json
POST https://descriptapi.com/v1/jobs/agent
Authorization: Bearer <DRIVE_SCOPED_TOKEN>
Content-Type: application/json

{
  "project_id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "composition_id": "39677a40-1c43-4c36-8449-46cfbc4de2b5",
  "prompt": "Add studio sound and captions",
  "team_access": "edit",
  "callback_url": "https://example.com/webhook"
}
```

**Response (201)**: `job_id`, `drive_id`, `project_id`, `project_url` (source: https://docs.descriptapi.com/).

**On completion** (`GET /jobs/{job_id}`):

```json
{
  "job_id": "project-agent-edit-e2f89ce6",
  "job_type": "agent",
  "project_id": "YOUR_PROJECT_ID",
  "job_state": "stopped",
  "result": {
    "status": "success",
    "agent_response": "Done! I've applied Studio Sound...",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 32
  }
}
```
(source: https://docs.descriptapi.com/)

**One-shot only:** "conversation and follow up questions aren't practical. So we recommend framing your edits as a one-shot prompt with all the information the agent needs" (source: https://docs.descriptapi.com/).

### 4.3 What the agent can do (the editing surface, expressed as prompts)

Documented example capabilities — "anything Underlord can do in the app, it can do through the API":

- **Create new content:** "create a 30-second video about cooking tips" (source: https://docs.descriptapi.com/)
- **Apply audio effects:** "add studio sound to every clip" (source: https://docs.descriptapi.com/)
- **Remove filler words:** "remove all filler words from the transcript" (source: https://docs.descriptapi.com/)
- **Create highlights / clips:** "create a 30-second highlight reel" (source: https://docs.descriptapi.com/)
- **Content editing by timecode:** "remove the section from 1:30 to 2:15" (source: https://docs.descriptapi.com/)
- **Captions:** e.g. "Add studio sound and captions" (source: https://docs.descriptapi.com/)
- Broader documented Underlord set: add Studio Sound, remove filler words, create highlight clips, add captions, generate B-roll, translate/dub audio, build rough cuts [secondary: search summary of https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta; also https://www.descript.com/api]

Note the timecode example ("remove the section from 1:30 to 2:15") shows that operations a direct API might expose as a typed `trim`/`cut` are instead achieved by *describing* them in the prompt — confirming there is no separate typed trim/cut endpoint (source: https://docs.descriptapi.com/).

### 4.4 Direct-operation matrix

| Operation | Direct (non-NL) API? | How it's actually done |
|---|---|---|
| Trim / cut by timecode | No — Not documented | Agent prompt (e.g. "remove the section from 1:30 to 2:15") (source: https://docs.descriptapi.com/) |
| Reorder existing clips | No — Not documented | Agent prompt (assembly order can be set at import via `clips[]`) (source: https://docs.descriptapi.com/) |
| Transitions | No — Not documented | Agent prompt (inferred) |
| Text overlays / titles / lower thirds | No — Not documented | Agent prompt (e.g. captions); lower-thirds Not documented as a typed op (source: https://docs.descriptapi.com/) |
| Music tracks | No — Not documented | Agent prompt (inferred) |
| Volume / audio mixing | No — Not documented | Studio Sound via agent prompt; per-track mixing Not documented (source: https://docs.descriptapi.com/) |
| Captions | No typed endpoint | Agent prompt "add captions" (source: https://docs.descriptapi.com/) |
| B-roll | No typed endpoint | Agent prompt "generate B-roll" [secondary: https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta] |
| Translate / dub | No typed endpoint | Agent prompt (source: https://www.descript.com/api) |
| Clip assembly / ordering at import | Partial (structural, not editing) | `add_compositions[].clips[].media` ordering (source: https://docs.descriptapi.com/) |

### 4.5 Related notes

- **Editing consumes AI credits**; the completion payload reports `ai_credits_used` and `media_seconds_used`. Running out of AI credits returns HTTP 402 [secondary: search summary of https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta] (and `ai_credits_used` is shown primary in the docs, source: https://docs.descriptapi.com/).
- **No render-the-video-to-a-file endpoint** exists outside publishing; the finished *rendered video* is surfaced by `POST /jobs/publish` (share URL + signed download URL) [secondary: https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta; https://www.descript.com/api]. **Timeline and transcript handoff, by contrast, ARE documented API endpoints** — `POST /export/timeline` (FCPXML / Premiere XML / EDL / AAF) and `POST /export/transcript` — returning files directly (see the export-endpoints note in 5.7 below) (source: https://docs.descriptapi.com/openapi-experimental.html). *(Correction: an earlier draft, following a secondary news source, said timeline/XML/AAF handoff was product-level only and not in the API — the OpenAPI spec shows it is a first-class API endpoint.)*
- **App vs API:** The app exposes a full manual timeline editor (split, layers, transitions, etc.) [secondary: search summaries of help.descript.com timeline/layer articles], but those manual operations are **not** exposed as API primitives — the API mediates them only through the agent.

## Section 5: Publish and Export

The finished **rendered video** is delivered through the **publish job** (`POST /jobs/publish` → share page + time-limited signed `download_url`); there is no separate endpoint that renders the video to a downloadable file outside publish. **But the API is not publish-only for output** — two direct, non-agent **export** endpoints return a file straight in the HTTP response body (with the exported composition UUID in the `X-Composition-Id` response header) (source: https://docs.descriptapi.com/openapi-experimental.html):

- **`POST /export/transcript`** — body includes `project_id` + a `format`; returns the raw transcript file with the format's `Content-Type`. Documented request-sample examples: *export as plain text with speaker labels*, *export as Markdown with timecodes*; **VTT** is referenced in the spec. **SRT is not shown.** Errors: 400 / 401 / 403 / 429.
- **`POST /export/timeline`** — body includes `project_id`; returns the raw editorial-timeline file for use in other NLEs/DAWs. Documented request-sample examples: *Final Cut Pro X (FCPXML)*, *Premiere Pro XML*, *Samplitude/Reaper EDL*; **AAF** and **DaVinci Resolve** are also referenced in the spec. Errors: 400 / 401 / 403 / 429.

These are **export/read operations, not editing** — they take existing project content *out*; they do not transform it. Credit/media-minute cost for them is **Not documented**. The exact boundary on the rendered *video* file is: it comes only via publish (below).

### 5.7 Output endpoints at a glance

| Endpoint | Method | Returns | Editing? |
|---|---|---|---|
| `/jobs/publish` | POST (async) | Hosted share page + signed video `download_url` | No (render/output) |
| `/export/transcript` | POST | Transcript file (text / Markdown / VTT) in response body | No (export) |
| `/export/timeline` | POST | Editorial timeline file (FCPXML / Premiere XML / EDL / AAF) in response body | No (export) |
| `/published_projects/{slug}` | GET | Published-project metadata + inline WEBVTT subtitles | No (read) |

---

### 5.1 The export boundary (CRITICAL)

**Direct local-file export is NOT supported by the API.** The only way to get rendered media out of the API is:

1. `POST /jobs/publish` -> creates an asynchronous publish job and immediately returns a `project_url`.
2. Poll `GET /jobs/{job_id}` until the job completes.
3. The completed result carries:
   - `share_url` - a persistent public/share page URL on Descript's share site (`share.descript.com` / `web.descript.com`).
   - `download_url` - a **time-limited signed URL** for direct download of the exported media file.
   - `download_url_expires_at` - the expiration timestamp of that signed URL.

> "When the job completes successfully the result contains both: `share_url`: a public URL that can be used to view the published content on Descript's share site. `download_url`: a time-limited signed URL to download the exported media file directly." (source: https://docs.descriptapi.com/)

There is **no** endpoint that streams a file directly, accepts a local upload for render-back, or writes to the caller's filesystem. The API does not expose the application's "Local Export" (export-to-disk) feature at all. Local export (export-to-MP4 on the user's machine, with High/Medium/Low presets) exists **only in the Descript app**, not in the API (source: https://help.descript.com/hc/en-us/articles/28868035473421-How-Descript-controls-export-quality-and-file-size). The API equivalent of "get my file" is always: publish to the cloud, then download via the signed `download_url`.

Note that this cloud-first path mirrors the app's own guidance: the app recommends Publish-then-download for long/high-resolution videos because cloud processing can be "up to ten times faster than local exports" [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255819601037-Export-and-publishing-overview).

---

### 5.2 `POST /jobs/publish` - full request

**Endpoint:** `POST https://descriptapi.com/v1/jobs/publish` (source: https://docs.descriptapi.com/)

**Auth:** `Authorization: Bearer <token>` (token created in Descript Settings > API tokens, scoped to a Drive, inheriting the user's permissions) (source: https://docs.descriptapi.com/)

#### Request parameters

| Field | Type | Required | Allowed values / notes |
|---|---|---|---|
| `project_id` | string (UUID) | Yes | The project to publish (source: https://docs.descriptapi.com/) |
| `composition_id` | string (UUID) | Yes* | Specific composition within the project. *The docs list it as a body parameter; one fetch read it as required, another as optional. Treat as required-in-practice for a deterministic publish, optional only if a project default exists.* (source: https://docs.descriptapi.com/) |
| `media_type` | string (enum) | No | `"Video"` or `"Audio"`. Default `"Video"` (source: https://docs.descriptapi.com/) |
| `resolution` | string (enum) | No | `"480p"`, `"720p"`, `"1080p"`, `"1440p"`, `"4K"`. **Applies to `media_type=Video` only**; not applicable to audio (source: https://docs.descriptapi.com/) |
| `access_level` | string (enum) | No | `"public"`, `"unlisted"`, `"drive"`, `"private"`. Defaults to the drive's configured setting (source: https://docs.descriptapi.com/) |
| `callback_url` | string (URI) | No | Webhook called on job completion (source: https://docs.descriptapi.com/) |

**Format options:** The API does not expose a container/codec selector (no MP4-vs-MOV switch). Media type is chosen via `media_type` (`Video` / `Audio`) and, for video, `resolution`. A `format` parameter is **Not documented** for `/jobs/publish`. (For context, the Descript app's *local* export produces MP4 for video, not MOV [secondary: descript.com] (source: https://www.descript.com/blog/article/how-to-export-a-video-and-which-format-to-use); the API's published media container is **Not documented** beyond the file referenced by `download_url`.)

**Audio export:** Selected with `media_type="Audio"`; in that case `resolution` does not apply (source: https://docs.descriptapi.com/). Per-track audio bitrate / normalization controls are **Not documented** for the API (these exist only in the app local-export UI [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/28868035473421-How-Descript-controls-export-quality-and-file-size)).

#### Example request

```http
POST https://descriptapi.com/v1/jobs/publish
Authorization: Bearer <DRIVE_SCOPED_TOKEN>
Content-Type: application/json

{
  "project_id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "composition_id": "1c0d...",
  "media_type": "Video",
  "resolution": "1080p",
  "access_level": "unlisted",
  "callback_url": "https://example.com/webhooks/descript"
}
```

---

### 5.3 `POST /jobs/publish` - response

**`201 Created`** returns the job handle plus a project URL immediately (the render itself is async):

```json
{
  "job_id": "6dc3f30a-58c2-4174-96a6-dc18cf3c7776",
  "drive_id": "c9c5c47e-158a-49f7-846b-4f6ee2a229a2",
  "project_id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "project_url": "https://web.descript.com/9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb"
}
```
(source: https://docs.descriptapi.com/)

**On completion (poll `GET /jobs/{job_id}`)** the result adds:

| Field | Meaning |
|---|---|
| `share_url` | Persistent shareable link on Descript's share site (source: https://docs.descriptapi.com/) |
| `download_url` | Time-limited signed URL to download the exported media file directly (source: https://docs.descriptapi.com/) |
| `download_url_expires_at` | Expiration timestamp for the signed `download_url` (source: https://docs.descriptapi.com/) |

#### HTTP status codes

| Code | Meaning |
|---|---|
| `201` | Job created (source: https://docs.descriptapi.com/) |
| `400` | Invalid input (malformed request, invalid format/resolution) (source: https://docs.descriptapi.com/) |
| `401` | Missing/invalid token (source: https://docs.descriptapi.com/) |
| `403` | No write access, or requested `access_level` not permitted by drive settings (source: https://docs.descriptapi.com/) |
| `422` | Unprocessable entity (source: https://docs.descriptapi.com/) |
| `429` | Rate limited - honor `Retry-After` (source: https://docs.descriptapi.com/) |

---

### 5.4 Access levels

| Value | Behavior |
|---|---|
| `public` | Publicly viewable; the app describes public pages as indexable by search engines [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/15210580318349-View-share-and-manage-a-published-share-page) |
| `unlisted` | Anyone with the link can view, not indexed by search engines [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/15210580318349-View-share-and-manage-a-published-share-page) |
| `drive` | Scoped to the drive's configured/default sharing setting (source: https://docs.descriptapi.com/) |
| `private` | Restricted; requesting it may be rejected (`403`) if the drive's settings disallow it (source: https://docs.descriptapi.com/) |

The app-side equivalents are public / unlisted / restrict-to-specified-members [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/15210580318349-View-share-and-manage-a-published-share-page). Password protection for share pages is **Not documented** as an API parameter (it appears as an open feature request) (source: https://help.descript.com/hc/en-us/articles/15210580318349-View-share-and-manage-a-published-share-page).

---

### 5.5 Resolution options

The API accepts `"480p"`, `"720p"`, `"1080p"`, `"1440p"`, `"4K"` for video (source: https://docs.descriptapi.com/). Available resolutions are gated by the user's **plan**, aspect ratio, and source quality; the app docs note the Free plan caps export at 720p, and upscaling beyond source resolution only inflates file size without improving quality [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255819601037-Export-and-publishing-overview). Whether the API enforces these plan caps (vs returning `400`/`403`) is **Not documented**; a too-high `resolution` is the kind of value a `400 Invalid input` covers (source: https://docs.descriptapi.com/).

---

### 5.6 Subtitle / caption export

- **Via the published-project metadata:** `GET /published_projects/{publishedProjectSlug}` returns a `subtitles` field containing **WEBVTT** text inline (e.g. `"subtitles": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nWelcome to my video"`) (source: https://docs.descriptapi.com/). This is the documented programmatic path to captions and is **VTT-formatted**.
- **Separate SRT vs VTT files:** A dedicated subtitle-export endpoint that lets you choose SRT vs VTT is **Not documented** in the REST API. The publish job result is not documented to include an SRT attachment. (source: https://docs.descriptapi.com/)
- **App-side (not API):** The Descript app exports subtitles via Export > Subtitles tab, supporting SRT, VTT and other formats, with options like max characters per line and lines per caption block [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255811669773-Export-subtitles). Videos downloaded from a published share page do **not** carry embedded/burned-in subtitles [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/33701193002125-Remove-Embedded-Subtitles-in-Locally-Exported-Videos).

**Boundary on subtitles:** programmatically, expect **inline WEBVTT** from the published-project endpoint; do not assume a separate downloadable SRT file from the API.

---

### 5.7 URL expiration

- `download_url` is a **time-limited signed URL**; its expiry is given by `download_url_expires_at` (an ISO-8601 timestamp, e.g. `2025-01-16T10:30:00.000Z`) (source: https://docs.descriptapi.com/).
- `share_url` / `project_url` are **persistent** (they survive republish and are reused) (source: https://docs.descriptapi.com/).
- The exact validity *duration* of the signed `download_url` (e.g. minutes/hours) is **Not documented** - only that an expiry timestamp is returned, so callers must read `download_url_expires_at` rather than assume a fixed TTL.

---

### 5.8 Republishing behavior

> "Publishing the same composition reuses previous `share_url`, overwriting content. Matching is keyed on `(project_id, composition_id, media_type)`." (source: https://docs.descriptapi.com/)

So re-running `POST /jobs/publish` for the same `(project_id, composition_id, media_type)` tuple **updates in place** and keeps the same share URL - identical to the app's "Update" button which refreshes content while preserving the original web-link URL [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255819601037-Export-and-publishing-overview). Changing `media_type` (Video vs Audio) keys a different publication.

---

### 5.9 Quick reference

| Concern | API answer |
|---|---|
| Local file export endpoint | None - publish-then-download only (source: https://docs.descriptapi.com/) |
| Output delivery | `share_url` (persistent) + signed `download_url` (expiring) (source: https://docs.descriptapi.com/) |
| Container/codec selector | Not documented (source: https://docs.descriptapi.com/) |
| Resolutions | 480p/720p/1080p/1440p/4K, video only (source: https://docs.descriptapi.com/) |
| Audio export | `media_type="Audio"`, no resolution (source: https://docs.descriptapi.com/) |
| Subtitles | Inline WEBVTT via `GET /published_projects/{slug}` (source: https://docs.descriptapi.com/) |
| URL expiry | `download_url_expires_at` returned; duration Not documented (source: https://docs.descriptapi.com/) |
| Republish | Same `share_url`, keyed on (project_id, composition_id, media_type) (source: https://docs.descriptapi.com/) |

## Section 6: Partner APIs

Descript exposes **two partner-only capabilities** that are distinct from, and gated more tightly than, the open REST API: **"Edit in Descript"** (importing files *into* Descript from a partner) and **"Export from Descript"** (reading published-project metadata *back out* to a partner). Both are part of the **supported content-hosting partner** program and require contacting Descript.

---

### 6.0 CRITICAL: distinguishing the open REST API from the partner-only feature

Third-party write-ups frequently **blur** two different things. Keep them separate:

| | Open REST API | Partner-only "Edit in Descript" |
|---|---|---|
| What it is | `POST /jobs/import/project_media`, `POST /jobs/agent`, `POST /jobs/publish`, `GET/DELETE /jobs`, `GET /projects`, etc. | `POST /edit_in_descript/schema` + `GET /published_projects/{slug}` (source: https://docs.descriptapi.com/) |
| Who can use it | Any user with a drive-scoped Bearer token from Settings > API tokens (source: https://docs.descriptapi.com/) | Only approved partners; Descript must add your service to the app as a final step (source: https://docs.descriptapi.com/) |
| Gating | Self-service token | **Requires contacting Descript** - "requires contacting Descript for access. Reach out to us at descript.com/api to get started." (source: https://docs.descriptapi.com/) |
| Purpose | Programmatic import / edit-agent / publish for your own drive | Let *your product's users* round-trip media into and out of Descript |

The `403` on `POST /edit_in_descript/schema` literally means "Drive lacks Edit in Descript permissions" - i.e. the open token works for `/jobs/*` but is **not** automatically entitled to the partner schema endpoint (source: https://docs.descriptapi.com/). Sources like apitracker.io contain no endpoint detail at all and merely point back to Descript's docs (it "describes neither specific feature category") (source: https://apitracker.io/a/descript); broader tutorials that lump "publish web links" together with "Edit in Descript" as one product are conflating the open and partner tiers [secondary: agentsapis.com / solomonsignal.com] (source: https://docs.descriptapi.com/).

---

### 6.1 "Edit in Descript" - `POST /edit_in_descript/schema`

**How the integration works:** A partner's backend calls this endpoint with a project schema describing source files; Descript responds with a **one-time-use import URL** to its import UI. The partner sends the user to that URL, landing them in a Descript project pre-populated with the imported media. This removes the download-then-reupload step (source: https://docs.descriptapi.com/; integration overview [secondary: help.descript.com] https://help.descript.com/hc/en-us/articles/12942138820621-Importing-files-using-Edit-in-Descript-beta).

> Descript "eliminates the tedious and error-prone process of downloading files to your computer and re-uploading them into Descript... with Edit in Descript. This integration lets you import files from one of our partners directly into the app. The Edit in Descript integration is in beta." [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/12942138820621-Importing-files-using-Edit-in-Descript-beta)

**Endpoint:** `POST https://descriptapi.com/v1/edit_in_descript/schema` (source: https://docs.descriptapi.com/)

**Request body:**
```json
{
  "partner_drive_id": "aa3b2d8d-e5ca-41c2-8801-683328293fb9",
  "project_schema": {
    "schema_version": "1.0.0",
    "source_id": "06b3f5bb-68a2-468a-a5d9-df155c1e3588",
    "files": [
      {
        "name": "Jane's studio recording",
        "uri": "https://...",
        "start_offset": { "seconds": 10 }
      }
    ]
  }
}
```
(source: https://docs.descriptapi.com/)

**Response (`201 Created`):**
```json
{ "url": "https://web.descript.com/import?nonce=6e3625ca-f885-460d-b2e3-e3faac79c788" }
```
(source: https://docs.descriptapi.com/)

**Key rules:**
- Import URLs are **one-time use** and **expire after 3 hours** (source: https://docs.descriptapi.com/).
- Source `files[].uri` must be **public or pre-signed**; Descript recommends ~**48-hour** validity on pre-signed URIs (source: https://docs.descriptapi.com/).
- Supported media for import: **WAV, FLAC, AAC, MP3** (audio); **h264, HEVC in MOV/MP4** (video) (source: https://docs.descriptapi.com/).
- `partner_drive_id` and `source_id` are the round-trip identifiers carried through to export (see 6.2).

**Status codes:** `201` success; `400` invalid input; `403` drive lacks Edit in Descript permissions; `429` rate limited (source: https://docs.descriptapi.com/).

---

### 6.2 "Export from Descript" - `GET /published_projects/{publishedProjectSlug}`

**Endpoint:** `GET https://descriptapi.com/v1/published_projects/{publishedProjectSlug}` (source: https://docs.descriptapi.com/)

Returns metadata and a signed download URL for a published project, so a partner can pull the finished asset back into its own system.

**Response (`200 OK`):**
```json
{
  "download_url": "https://storage.googleapis.com/bucket/file.mp4?X-Goog-Signature=...",
  "download_url_expires_at": "2025-01-16T10:30:00.000Z",
  "project_id": "12345678-1234-5678-1234-567812345678",
  "publish_type": "video",
  "privacy": "unlisted",
  "metadata": {
    "title": "My Video Project",
    "duration_seconds": 125.456,
    "duration_formatted": "00:02:05",
    "published_at": "2025-01-15T10:30:00.000Z",
    "published_by": { "first_name": "Jane", "last_name": "Doe" }
  },
  "subtitles": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nWelcome to my video"
}
```
(source: https://docs.descriptapi.com/)

**Available metadata:** `download_url`, `download_url_expires_at`, `project_id`, `publish_type` (e.g. `video`), `privacy` (e.g. `unlisted`), `metadata.title`, `metadata.duration_seconds`, `metadata.duration_formatted`, `metadata.published_at`, `metadata.published_by.{first_name,last_name}`, and inline `subtitles` (WEBVTT) (source: https://docs.descriptapi.com/).

**Status codes:** `200` success; `401` missing/invalid token; `403` no access; `404` not found; `409` project in invalid state (processing/failed); `429` rate limited (documented as **1000 requests/hour per user**) (source: https://docs.descriptapi.com/).

#### Round-trip metadata (`partner_drive_id` / `source_id`)

> "If Project data previously came from a partner via an Edit in Descript schema then any Descript Export pages will include `<meta/>` tags which contains the `partner_drive_id` and `source_id` provided when originally importing into Descript." This "allows partners to deduplicate data returning back to partner systems after editing in Descript." (source: https://docs.descriptapi.com/)

The export/share page HTML carries these as meta tags, for example:
```html
<meta property="descript:partner" content="9121bf3a-60fe-4a31-ad59-ed32df610cc3" />
<meta property="descript:source"  content="409148bd-81aa-4af6-a9c3-29b761506f3a" />
```
(source: https://docs.descriptapi.com/)

So the `partner_drive_id` and `source_id` a partner supplies on import in 6.1 come back out on export, letting the partner match the returned asset to the original record.

---

### 6.3 Becoming a partner / access requirements

- **The Edit in Descript / content-hosting partner program is gated.** "The Edit in Descript integration requires contacting Descript for access. Reach out to us at descript.com/api to get started." (source: https://docs.descriptapi.com/)
- Onboarding is **"largely self-service,"** but you **must tell Descript** you are building an integration because "they'll need to add you to the app as a final step"; Descript can also provide technical support [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255610365837-Becoming-a-supported-content-hosting-partner).
- Auth uses the same drive-scoped Bearer token model, but the token's drive must be **entitled** for Edit in Descript or `/edit_in_descript/schema` returns `403` (source: https://docs.descriptapi.com/).

---

### 6.4 How the export-handoff flow works (content-hosting partner)

When a Descript user finishes content, the media is uploaded to a `share.descript.com` page and the user is shown a confirmation dialog with **quick links** to push the media to their content host. Once you are a supported partner, your service appears as one of those quick links [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255610365837-Becoming-a-supported-content-hosting-partner).

- The quick link directs the user to **your episode-creation URL with an argument that lets you fetch the file from Descript** [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255610365837-Becoming-a-supported-content-hosting-partner).
- The user may click through **before the export finishes**, so Descript recommends **polling the meta links until the asset is found, every 30 seconds to 1 minute, with a max timeout of 3 hours** [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255610365837-Becoming-a-supported-content-hosting-partner).
- Existing supported export/host partners (app quick-links, not API names) include Buzzsprout, Captivate, Castos, eWebinar, Hello Audio, Headliner, Podbean, Podcast.co, Restream, and Transistor [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255815785613-Publishing-to-other-hosting-providers).

---

### 6.5 Use cases

- **Recording / capture tools** (e.g. SquadCast) pushing raw recordings straight into a Descript edit session via Edit in Descript [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/12942138820621-Importing-files-using-Edit-in-Descript-beta).
- **Podcast / video hosts** receiving finished exports as a one-click destination from Descript's publish dialog, then pulling the rendered file via the partner export flow [secondary: help.descript.com] (source: https://help.descript.com/hc/en-us/articles/10255610365837-Becoming-a-supported-content-hosting-partner).
- **Round-trip dedup:** a partner that both feeds files in and reads exports back uses `partner_drive_id` + `source_id` meta tags to reconcile the returned asset with the original (source: https://docs.descriptapi.com/).

---

### 6.6 Gaps for the partner section

- Exact partner-approval criteria / SLA / contract terms: **Not documented** (only "contact us").
- Whether `/edit_in_descript/schema` supports more than the listed codecs/containers, or multiple `source_id`s per request: **Not documented**.
- Whether `GET /published_projects/{slug}` requires a partner token vs any drive token: only the `401`/`403` codes are documented; the entitlement detail is **Not documented**.

## Section 7: Webhooks and Async Patterns

The Descript API is fundamentally **asynchronous and job-based**. Long-running operations (media import, AI agent edits, publishing) do not block; instead they return a job that you either **poll** via the jobs endpoints or have **pushed back to you via a webhook callback**. This section documents both async patterns end to end.

---

### 7.1 Webhook Callbacks (`callback_url`)

All three job-creating endpoints accept an optional `callback_url` parameter. When supplied, Descript performs an HTTP **`POST`** to that URL once the job reaches a terminal state (completed **or** failed) (source: https://docs.descriptapi.com/).

| Job-creating endpoint | Accepts `callback_url`? | Trigger condition |
|---|---|---|
| `POST /jobs/import/project_media` | Yes (optional) | "when the job finishes (successfully or not)" |
| `POST /jobs/agent` | Yes (optional) | "when the job completes or fails" |
| `POST /jobs/publish` | Yes (optional) | Same pattern — fires on completion or failure |

Sources: https://docs.descriptapi.com/ — the documented wording is *"Optional webhook URL to call when the job completes or fails. Descript will POST the job status to that URL when the job finishes"* and *"we'll ping you when the job has stopped with the same response payload."*

**Key facts (all primary):**
- The parameter is named **`callback_url`** and is **optional** on every job-creating endpoint (source: https://docs.descriptapi.com/).
- Delivery method is **`POST`** (source: https://docs.descriptapi.com/).
- The webhook fires exactly once, when the job transitions to its terminal (`stopped`) state — whether the job succeeded or failed (source: https://docs.descriptapi.com/).

> **Not documented:** Whether Descript retries a failed webhook delivery, any delivery timeout, expected `2xx` acknowledgement behavior from your receiver, and any ordering/at-least-once guarantees. **Not documented.**

---

### 7.2 Webhook Payload Format

The webhook body **mirrors the job-status response**. The documentation states the payload *"will match the format returned by `GET /jobs/{job_id}`"* and that Descript pings you *"with the same response payload"* (source: https://docs.descriptapi.com/).

That means a delivered webhook contains the same fields you would receive by polling (see 7.3): `job_id`, `job_type`, `job_state`, `created_at`, `stopped_at`, `drive_id`, `project_id`, `project_url`, and the `result` object (source: https://docs.descriptapi.com/).

Illustrative payload (shape per documented fields; field *values* below are representative, not verbatim from docs):

```json
{
  "job_id": "f1e2d3c4-0000-4a5b-9c8d-7e6f5a4b3c2d",
  "job_type": "agent",
  "job_state": "stopped",
  "created_at": "2026-05-31T12:00:00Z",
  "stopped_at": "2026-05-31T12:03:42Z",
  "drive_id": "...",
  "project_id": "...",
  "project_url": "https://web.descript.com/...",
  "result": {
    "status": "success"
  }
}
```

> **Not documented:** Whether the webhook POST carries any signature header, shared secret, HMAC, or other authenticity mechanism. The documentation contains **no mention** of webhook signature verification. Treat inbound webhooks as **unauthenticated** unless you add your own out-of-band verification (e.g., a secret token embedded in the `callback_url` path/query). **Not documented.**

---

### 7.3 Job Status Polling — `GET /jobs/{job_id}`

The polling endpoint returns the current job record. This is the canonical way to track async work when you are not using a `callback_url` (source: https://docs.descriptapi.com/).

#### Documented `job_state` lifecycle

The documentation does not enumerate every state in a single list, but the states surfaced in examples are:

| `job_state` | Meaning |
|---|---|
| `running` | Job is actively processing (source: https://docs.descriptapi.com/) |
| `stopped` | Job has reached a terminal state — completed **or** failed (source: https://docs.descriptapi.com/) |

A `queued` state is *implied* by the design but is **not explicitly documented** as a distinct value — treat it as inferred, not confirmed.

#### Success vs. failure determination

The top-level `job_state` only tells you *running* vs. *stopped*. Once `job_state == "stopped"`, you must inspect the nested **`result.status`** field — `"success"` indicates the job completed successfully; other values indicate failure (source: https://docs.descriptapi.com/). This two-level model (lifecycle state vs. outcome) is important: a `stopped` job is **not** necessarily a successful job.

#### Documented response fields

| Field | Notes |
|---|---|
| `job_id` | The job identifier (source: https://docs.descriptapi.com/) |
| `job_type` | e.g. `import/project_media`, `agent` (source: https://docs.descriptapi.com/) |
| `job_state` | `running` / `stopped` (source: https://docs.descriptapi.com/) |
| `created_at` | Timestamp (source: https://docs.descriptapi.com/) |
| `stopped_at` | Terminal timestamp (source: https://docs.descriptapi.com/) |
| `drive_id` | Owning Drive (source: https://docs.descriptapi.com/) |
| `project_id` | Associated project (source: https://docs.descriptapi.com/) |
| `project_url` | Web link to the project (source: https://docs.descriptapi.com/) |
| `result` | Object containing `status` plus type-specific data (e.g. `agent_response`, `media_status`) (source: https://docs.descriptapi.com/) |
| `progress` | Present for running jobs; includes `label` and `last_update_at` (source: https://docs.descriptapi.com/) |

> **Polling guidance:** Because there is no documented `queued`/intermediate granularity beyond `running` and `progress.label`, a sensible pattern is to poll `GET /jobs/{job_id}` on a backoff interval until `job_state == "stopped"`, then branch on `result.status`. Webhooks (`callback_url`) are the preferred push alternative. The docs do not specify a recommended polling interval. **Not documented (polling interval).**

---

### 7.4 Job Cancellation — `DELETE /jobs/{job_id}`

Cancels/aborts a job. Documented responses:

| Code | Meaning |
|---|---|
| `204` | "Job cancelled successfully" — no response body (source: https://docs.descriptapi.com/) |
| `401` | Unauthorized — missing/invalid token (source: https://docs.descriptapi.com/) |
| `403` | Forbidden (source: https://docs.descriptapi.com/) |
| `404` | Job not found (source: https://docs.descriptapi.com/) |
| `429` | Rate limit exceeded (source: https://docs.descriptapi.com/) |

> **Not documented:** What happens when you `DELETE` a job that has **already finished** (i.e., already `stopped`). The documentation does not address idempotency or the behavior of cancelling a terminal job. **Not documented.**

---

### 7.5 Job Listing & Filtering — `GET /jobs`

Returns a paginated list of jobs, filterable by project, type, and creation time, with a bounded lookback window (source: https://docs.descriptapi.com/).

#### Query parameters

| Parameter | Description | Default | Constraints |
|---|---|---|---|
| `project_id` | Filter by project ID (UUID) | — | UUID format (source: https://docs.descriptapi.com/) |
| `type` | Filter by job type | — | Enum: `import/project_media`, `agent` (source: https://docs.descriptapi.com/) |
| `created_after` | Filter jobs created after this ISO-8601 timestamp | `7 days ago` | Oldest allowed: `30 days ago` (source: https://docs.descriptapi.com/) |
| `created_before` | Filter jobs created before this ISO-8601 timestamp | `now` | — (source: https://docs.descriptapi.com/) |
| `cursor` | Pagination cursor for next page | — | From `pagination.next_cursor` (source: https://docs.descriptapi.com/) |
| `limit` | Items per page | `20` | Range `1`–`100` (source: https://docs.descriptapi.com/) |

#### Lookback window

- **Default window:** jobs created within the **last 7 days** are returned (source: https://docs.descriptapi.com/).
- **Maximum lookback:** **30 days** — `created_after` cannot be older than 30 days ago (source: https://docs.descriptapi.com/).

#### Pagination

Responses include a `pagination` object with `next_cursor`; pass it as the `cursor` query parameter to retrieve the next page (source: https://docs.descriptapi.com/).

> **Note on `type` enum:** The documented enum lists only `import/project_media` and `agent`. A `publish` filter value is **not documented** for `GET /jobs`, even though `POST /jobs/publish` exists. **Not documented (publish as a list filter).**

---

### 7.6 Recommended async workflow (synthesis)

1. `POST` to a job-creating endpoint, optionally supplying `callback_url`.
2. **Either** wait for the webhook `POST` (payload == job-status response) **or** poll `GET /jobs/{job_id}` until `job_state == "stopped"`.
3. On terminal, branch on `result.status` (`"success"` vs. failure).
4. Use `GET /jobs` (with `type`/`project_id`/`created_after` filters, ≤30-day lookback) for reconciliation/auditing.
5. Use `DELETE /jobs/{job_id}` to cancel in-flight work.

> Because webhook authenticity and retry behavior are **Not documented**, polling remains the more defensively-verifiable pattern for production; if relying on webhooks, embed your own secret in `callback_url` and treat the body as untrusted until verified against a follow-up `GET /jobs/{job_id}`.

## Section 8: Authentication and Rate Limits

### 8.1 Authentication model

The Descript API uses a **personal Bearer token** sent in the HTTP `Authorization` header. The base URL is `https://descriptapi.com/v1` (source: https://docs.descriptapi.com/).

Documented usage: *"Include the token as a Bearer token in the `Authorization` header of your API requests"* (source: https://docs.descriptapi.com/).

```http
Authorization: Bearer <YOUR_DESCRIPT_API_TOKEN>
```

#### Token creation & management

Documented steps (source: https://docs.descriptapi.com/; corroborated [secondary: help.descript.com Descript API (beta), https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta]):

1. In Descript, open **Settings** and select **API tokens** from the sidebar.
2. Click **Create token**.
3. Give the token a **name** and select the **Drive** it should be associated with.
4. **Copy and store the token securely** — *"you won't be able to view it again"* [secondary: help.descript.com, https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta].

Security guidance from the docs: *"Treat your API token like a password... Never share your token publicly or commit it to source control"* (source: https://docs.descriptapi.com/).

#### Drive scoping & permission inheritance

- Tokens are **scoped to a specific Drive** and **inherit your permissions on that Drive**: *"Tokens are scoped to a specific Drive and inherit your permissions on that Drive"* (source: https://docs.descriptapi.com/).
- A single token cannot span multiple Drives — *"Tokens are scoped to a single drive. You'll need separate tokens for each drive"* [secondary: help.descript.com Descript API (beta), https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta].
- This scoping means a token can only create/read jobs and projects on its associated Drive, and its effective capabilities are bounded by the granting user's role on that Drive (permission inheritance) (source: https://docs.descriptapi.com/).

#### Relationship to the MCP integration

The Descript **MCP** (Model Context Protocol) integration for AI assistants is a **separate, distinct** auth path from the REST API token. The MCP connects through your **Descript login** (OAuth-style session), so *"there's no API token needed when using the MCP with an AI assistant."* The MCP is likewise **scoped to a single Drive** — to switch Drives you log out on the web and reconnect the MCP [secondary: help.descript.com "Use Descript with an AI assistant (MCP)", https://help.descript.com/hc/en-us/articles/46056322186509-Use-Descript-with-an-AI-assistant-MCP]. (The dedicated help article returned HTTP 403 to direct fetch; content captured via WebSearch surfacing of the same article.)

> The two share the **single-Drive scoping principle**, but the REST API requires an explicit Bearer token while the MCP rides the interactive login session.

---

### 8.2 Rate limits

#### General rate limiting

The API implements rate limiting and returns **`429 Too Many Requests`** when exceeded: *"The Descript API implements rate limiting to ensure fair usage and protect service availability. When you exceed the rate limit, the API returns a `429 Too Many Requests` response"* (source: https://docs.descriptapi.com/).

#### The 1000/hour figure is endpoint-scoped, NOT global

**Confirmed:** The **1000 requests/hour** number applies **only** to the published-project metadata endpoint, **not** globally. The figure appears **exclusively** in the section describing `GET /published_projects/{publishedProjectSlug}`:

> *"This endpoint requires authentication using a personal token and is subject to rate limiting of 1000 requests per hour per user."* (source: https://docs.descriptapi.com/)

For **all other endpoints**, the documentation confirms rate limiting **exists** but **does not publish a specific numeric threshold** (no per-minute or per-hour number is given) (source: https://docs.descriptapi.com/). Do **not** generalize the 1000/hour figure to the core job/project endpoints.

| Scope | Documented limit |
|---|---|
| `GET /published_projects/{publishedProjectSlug}` | **1000 requests / hour / user** (source: https://docs.descriptapi.com/) |
| All other endpoints (jobs, projects, etc.) | Rate-limited, but **specific number Not documented** (source: https://docs.descriptapi.com/) |

> Secondary directory/review sources do not publish any concrete numbers either — apitracker.io lists rate limits as not specified [secondary: apitracker.io, https://apitracker.io/a/descript], and the Cleanvoice review gives no rate-limit numbers (only that import URLs expire after three hours) [secondary: cleanvoice.ai, https://cleanvoice.ai/blog/descript-api-review/]. This corroborates that no global numeric limit is published.

#### Rate-limit response headers

Returned on a `429` response (source: https://docs.descriptapi.com/):

| Header | Documented description |
|---|---|
| `Retry-After` | "Number of seconds to wait before retrying the request" |
| `X-RateLimit-Remaining` | "Number of requests remaining in the current window" |
| `X-RateLimit-Consumed` | "Number of requests consumed in the current window" |

> Note the slightly unusual pairing of `X-RateLimit-Remaining` + `X-RateLimit-Consumed` (Descript exposes *consumed* rather than the more common `X-RateLimit-Limit`/`X-RateLimit-Reset`). A `X-RateLimit-Limit` or `X-RateLimit-Reset` header is **Not documented**.

#### Handling 429 / best practices

- On `429`, **read `Retry-After`** and wait that many seconds before retrying: *"When you receive a `429` response, use the `Retry-After` header to determine how long to wait before retrying"* (source: https://docs.descriptapi.com/).
- Monitor `X-RateLimit-Remaining` proactively to throttle before hitting the limit (source: https://docs.descriptapi.com/).
- **No specific retry strategy** (exponential backoff, jitter, max-attempts) is prescribed by the docs beyond honoring `Retry-After`. Exponential backoff is a sensible *inferred* best practice but is not documented. (source: https://docs.descriptapi.com/)

---

### 8.3 Documented error codes

| Code | Meaning | Documented wording / context |
|---|---|---|
| `401 Unauthorized` | Missing or invalid token | *"Unauthorized - missing or invalid authentication token"* (source: https://docs.descriptapi.com/) |
| `402 Payment Required` | Insufficient funds/credits to start the job | Import: *"insufficient media minutes to start the job"*; Agent: *"insufficient AI credits to start the job"* (source: https://docs.descriptapi.com/) |
| `403 Forbidden` | Agent usage disabled by Drive admin (on the agent endpoint) | *"the drive admin has disabled agent usage for this drive"* (source: https://docs.descriptapi.com/) |
| `404 Not Found` | Resource (e.g., job) not found | Returned by `DELETE /jobs/{job_id}` among others (source: https://docs.descriptapi.com/) |
| `429 Too Many Requests` | Rate limit exceeded | Use `Retry-After` to back off (source: https://docs.descriptapi.com/) |

**Notes on the credit/quota errors (402):** Descript distinguishes two resource pools — **media minutes** (consumed by `import/project_media`) and **AI credits** (consumed by `agent`). A `402` tells you *which* pool is depleted via its message, but the docs do not document the numeric balances or where to top them up via API. The remaining-balance values are **Not documented**.

**Notes on 403 (agent disabled):** This is specifically a **Drive-admin policy** control on the agent endpoint — distinct from a `401` (bad token) or a generic permissions failure. It reflects the permission-inheritance model: even a valid token cannot run agent jobs if the Drive admin disabled agent usage for that Drive (source: https://docs.descriptapi.com/).

---

### 8.4 Summary table

| Concern | Documented value |
|---|---|
| Auth scheme | Bearer token in `Authorization` header (source: https://docs.descriptapi.com/) |
| Base URL | `https://descriptapi.com/v1` (source: https://docs.descriptapi.com/) |
| Token scope | Single Drive; inherits user's Drive permissions (source: https://docs.descriptapi.com/) |
| Token visibility | Shown once at creation; store securely [secondary: help.descript.com, https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta] |
| MCP auth | No token; uses Descript login; single-Drive [secondary: help.descript.com, https://help.descript.com/hc/en-us/articles/46056322186509-Use-Descript-with-an-AI-assistant-MCP] |
| Global rate limit number | **Not documented** (rate limiting exists; no number) (source: https://docs.descriptapi.com/) |
| Published-project endpoint limit | 1000 req/hr/user (source: https://docs.descriptapi.com/) |
| Rate headers | `Retry-After`, `X-RateLimit-Remaining`, `X-RateLimit-Consumed` (source: https://docs.descriptapi.com/) |

## Section 9: Pricing and Credits

Descript pricing has **two distinct layers** that must not be conflated:

1. **What the API actually bills** — abstract usage units (media minutes + AI credits) reported in job responses. This is the only billing model the API itself exposes. (primary)
2. **What those units cost in dollars** — set by your underlying Descript subscription plan and published only on the marketing/pricing pages and third-party breakdowns. **Every dollar and per-feature credit figure below is labeled `[secondary: …]`** because Descript's primary API docs do not publish dollar prices.

---

### 9.1 Tier (a): What the API Bills (PRIMARY)

The API meters two resource types. Both are drawn from the pool attached to the **Drive** that your Bearer token is scoped to. (source: https://www.descript.com/api)

| Resource | Unit | Consumed by | Response field |
|---|---|---|---|
| **Media minutes** | seconds of media processed | importing / processing media (e.g. `POST /jobs/import/project_media`) | `media_seconds_used` |
| **AI credits** | abstract credit | AI editing operations (e.g. `POST /jobs/agent`) | `ai_credits_used` |

- `media_seconds_used` and `ai_credits_used` appear in **job responses after completion**, reporting actual consumption for that job. (source: https://docs.descriptapi.com/)
- The docs do not state a conversion ratio between `media_seconds_used` and "media minutes," but the unit is seconds; 60 seconds = 1 media minute is the obvious implication (inferred — not stated verbatim).

#### 402 Payment Required

When the Drive's balance is insufficient to start a job, the API returns **`402 Payment Required`** *before* the job runs. Two documented variants: (source: https://docs.descriptapi.com/)

- `402 Payment Required — insufficient media minutes to start the job` (on the media import endpoint)
- `402 Payment Required — insufficient AI credits to start the job` (on the agent endpoint)

This is a pre-flight balance check: the job is rejected up front rather than failing midway.

#### Free vs. paid API access

- **API access is available to all paying Descript users at no additional charge** — there is no separate API SKU or standalone API price. (source: https://www.descript.com/api)
- **"Usage draws from the AI credits and media minutes on your plan."** The API is not a distinct billable product; it consumes the same pool as the desktop/web app. (source: https://www.descript.com/api)
- The API is currently in **Early Access / beta**. (source: https://www.descript.com/enterprise/api-early-access ; https://help.descript.com/hc/en-us/articles/43370311322509-Descript-API-beta)
- One labeled secondary source phrases the access requirement as: *"There is no separate free trial for the API, and access requires joining a Descript plan and obtaining a token… API access is tied directly to Descript's regular platform plans, so no standalone API pricing is available outside of these tiers."* [secondary: cleanvoice.ai] (source: https://cleanvoice.ai/blog/descript-api-pricing/)
- Whether the **Free ($0) plan** can obtain an API token, or whether a paid plan is strictly required, is **not stated unambiguously in the primary docs**. descript.com/api says access is for "all paying users"; one secondary source lists Free among API-eligible plans. Treat the Free-tier API question as **Not documented** at the primary level.

---

### 9.2 Tier (b): What It Costs in Dollars (SECONDARY — all figures labeled)

> The Descript **primary API docs publish NO dollar prices**. Everything in 9.2 comes from the public pricing page and third-party breakdowns and is labeled secondary. Pricing reflects the overhaul that took effect **September 23, 2025**. [secondary: cotovan.com] (source: https://cotovan.com/post/descript-pricing-media-minutes-ai-credits-topups/)

#### Plan tiers (per seat)

| Plan | Monthly (billed monthly) | Monthly (billed annually) | Media | AI credits | Max export | Seats |
|---|---|---|---|---|---|---|
| **Free** | $0 | $0 | 60 min (1 hr)/mo | 100 (one-time) | 720p | 1 |
| **Hobbyist** | $24 | $16 | 10 media hrs/mo | 400/mo | 1080p, watermark-free | 1 |
| **Creator** (most popular) | $35 | $24 | 30 media hrs/mo (+5 bonus) | 800/mo (+500 bonus) | 4K, watermark-free | up to 3 |
| **Business** | $65 | $50 | 40 media hrs/mo (+10 bonus) | 1,500/mo (+1,000 bonus) | (not stated) | up to 5 |
| **Enterprise** | Custom | Custom | Custom media minutes & credits | Custom | — | — |

[secondary: descript.com/pricing] (source: https://www.descript.com/pricing). Annual-billing dollar figures and tier limits also corroborated by [secondary: cleanvoice.ai] (https://cleanvoice.ai/blog/descript-api-pricing/) and [secondary: cotovan.com].

Note: descript.com/pricing displays annual-billed-equivalent prices ($16/$24/$50) with a note that annual billing "saves up to 35%"; the higher numbers ($24/$35/$65) are the month-to-month rate. Both sets are quoted verbatim from the page. (source: https://www.descript.com/pricing)

Enterprise adds Advanced Security, SSO/SCIM, granular brand controls, custom legal terms, and flexible licensing/billing. [secondary: descript.com/pricing] (source: https://www.descript.com/pricing)

#### How usage is measured (secondary detail)

- **Media minutes count every file uploaded**, regardless of whether it is transcribed. Uploading two files for the same hour of content drains two media-minutes per minute of content. The per-seat allocations pool together across the Drive. [secondary: cotovan.com] (source: https://cotovan.com/post/descript-pricing-media-minutes-ai-credits-topups/)
- **AI credits** meter AI features (Underlord/AI Actions, Studio Sound, filler-word removal, silence/gap shortening, Eye Contact, Green Screen, AI-generated B-roll, avatars, translation/dubbing). Each seat adds to a shared Drive pool. [secondary: cotovan.com] (source: https://cotovan.com/post/descript-pricing-media-minutes-ai-credits-topups/)
- **No monthly rollover** of unused media minutes or AI credits as of Sept 2025. [secondary: cotovan.com] (source: https://cotovan.com/post/descript-pricing-media-minutes-ai-credits-topups/)

#### Top-up (overage) pricing — Creator & Business only

| Media top-up | Price | AI-credit top-up | Price |
|---|---|---|---|
| 5 hours | $25 | 350 credits | $35 |
| 20 hours | $80 | 1,000 credits | $80 |
| 50 hours | $150 | 4,000 credits | $200 |

Top-ups are one-time purchases from the Usage tab in App Settings, shared across the Drive, and (per this source) **roll over up to 12 months**. Top-ups are **not available on Free or Hobbyist** plans. [secondary: cotovan.com] (source: https://cotovan.com/post/descript-pricing-media-minutes-ai-credits-topups/)

#### Per-feature AI credit costs (secondary, low confidence)

Descript's primary help docs do **not** publish a verbatim per-feature credit table. Third-party estimates report:

| Feature | Approx. cost | Source confidence |
|---|---|---|
| Studio Sound | ~10 credits/use | [secondary: gptprompts.ai / aggregated search] |
| Eye Contact | ~10 credits/use | [secondary] |
| Green Screen | ~15 credits/use | [secondary] |
| Overdub (voice cloning) | ~5 credits/min | [secondary] |

Cross-checked sanity: Creator's 800 credits is widely described as covering "about 40 Studio Sound uses," consistent with ~10 credits/use. [secondary: aggregated] (source: https://gptprompts.ai/ai-pricing/descript-pricing). Treat these numbers as estimates — the official help article (help.descript.com/.../27841674958221) was inaccessible (HTTP 403) during research and could not confirm them.

---

### 9.3 API-specific cost questions

| Question | Answer |
|---|---|
| Cost per export at different resolutions (via API) | **Not documented.** Export resolution caps are tied to plan tier (720p Free / 1080p Hobbyist / 4K Creator), but no per-export credit or dollar cost is published. |
| Cost per agent edit job (`POST /jobs/agent`) | **Not documented** as a fixed price. It consumes `ai_credits_used` reported per job; the credit-to-job amount depends on the operations the agent performs and is returned only after completion. (source: https://docs.descriptapi.com/) |
| Cost per media import job | **Not documented** as a fixed price. Consumes `media_seconds_used`, reported per job. (source: https://docs.descriptapi.com/) |
| Standalone API price / separate API plan | **Does not exist.** API is included with paid plans at no extra cost. (source: https://www.descript.com/api) |

---

### 9.4 Summary

- **Bill model (primary):** media minutes + AI credits, drawn from the token's Drive pool; reported per job as `media_seconds_used` / `ai_credits_used`; `402` pre-flight rejection on insufficient balance.
- **Dollar model (secondary):** no API-specific pricing — you pay for a Descript plan (Free→Business $0/$16–24/$24–35/$50–65, Enterprise custom) and the API draws down that plan's quota. Overages via Creator/Business top-ups.
- **Unknowns:** exact credits per agent job, per-export costs, and authoritative per-feature credit table are **Not documented** in primary sources.

## Section 10: CLI (descript-api / @descript/platform-cli)

Descript ships an official command-line wrapper around the REST API. It is published to npm as **`@descript/platform-cli`** and exposes the binary **`descript-api`**.

---

### 10.1 Installation & Requirements

```bash
npm install -g @descript/platform-cli@latest
```

- **Runtime:** Node.js **24 or higher** is required. (source: https://docs.descriptapi.com/)
- **Language / SDK:** This is a **Node.js CLI only**. The primary docs document **no Python SDK** and no SDK in any other language — the CLI plus the raw REST API are the only documented integration surfaces. (source: https://docs.descriptapi.com/). A Python SDK is therefore **Not documented**.
- Installs globally (`-g`), providing the `descript-api` command on `PATH`. (source: https://docs.descriptapi.com/)

> Note: the npm registry page (https://www.npmjs.com/package/@descript/platform-cli) returned HTTP 403 during research, so version numbers and the package README could not be read directly; all CLI facts below come from the primary docs at docs.descriptapi.com. Current published version is **Not documented** here.

---

### 10.2 Authentication / Config Setup

Configure the API key once via the config subcommand:

```bash
descript-api config set api-key
```

- This stores the Bearer API key the CLI uses for all subsequent calls. (source: https://docs.descriptapi.com/)
- The key is the same Drive-scoped Bearer token used for the raw REST API. (inferred from the shared auth model; the CLI uses it transparently) (source: https://docs.descriptapi.com/)
- The setup flow is described as **interactive** — running the CLI provides "interactive flows for setting up authentication, importing, and prompting the agent." (source: https://docs.descriptapi.com/)

---

### 10.3 Commands & Options

| Command | Purpose | Documented flags |
|---|---|---|
| `descript-api config set api-key` | Store/set the API key | (interactive prompt) |
| `descript-api import` | Create a project and import media, with live progress; outputs the project ID when done | `--name "<project name>"`, `--media "<media URL>"` |
| `descript-api agent` | Run an AI (Underlord) edit on an existing project | `--project-id <ID>`, `--prompt "<instruction>"` |
| `descript-api edit --new` | Create a **new** project from a text prompt alone (Underlord writes the script) | `--new`, `--prompt "<instruction>"` |
| `descript-api help` | List all commands and their options | — |

(source: https://docs.descriptapi.com/)

#### Example invocations

```bash
# One-time auth
descript-api config set api-key

# Import media into a new project
descript-api import --name "Episode 12" --media "https://example.com/raw.mp4"

# Run an agent edit on an existing project
descript-api agent \
  --project-id proj_abc123 \
  --prompt "Remove filler words and add Studio Sound to all clips"

# Create a brand-new project from a prompt only (no source media)
descript-api edit --new --prompt "Write and assemble a 60s explainer about our launch"
```

(Example prompt text "Remove filler words and add Studio Sound to all clips" is quoted from the docs; source: https://docs.descriptapi.com/)

#### Interactive mode

- Running a command **without arguments** (e.g. `descript-api import`) drops into an **interactive flow** that prompts for the needed values. (source: https://docs.descriptapi.com/)
- The CLI as a whole offers interactive flows for auth, import, and agent prompting. (source: https://docs.descriptapi.com/)

---

### 10.4 Differences vs. the Raw API

The docs frame the CLI as a thin convenience layer over the same REST endpoints. Key differences: (source: https://docs.descriptapi.com/)

| Aspect | Raw API | CLI |
|---|---|---|
| Job submission | You `POST` to `/jobs/...` yourself and parse JSON | One command per workflow (`import`, `agent`, `edit --new`) |
| Job completion | You **poll** `GET /jobs/{job_id}` until status resolves | **Built-in polling** — the CLI waits for completion automatically |
| Progress feedback | None (you implement it) | **Live progress display**; prints project ID on completion |
| Auth | Send `Authorization: Bearer <token>` header on every request | `config set api-key` once, applied automatically |
| UX | Programmatic / scripted | Interactive prompts when args omitted |

Net: the CLI does not expose new capability beyond the API — it wraps the **import → agent/edit → poll** loop into single commands with polling and progress baked in.

---

### 10.5 Common Workflows

1. **First-time setup**
   ```bash
   npm install -g @descript/platform-cli@latest
   descript-api config set api-key   # paste Bearer token
   ```
2. **Ingest → edit existing project**
   ```bash
   PID=$(descript-api import --name "Show" --media "https://.../in.mp4")
   descript-api agent --project-id "$PID" --prompt "Cut silences, add captions"
   ```
   (The CLI prints the project ID after import; capture it for the agent step.)
3. **Script-to-project (no source media)**
   ```bash
   descript-api edit --new --prompt "Make a 90-second product teaser script and assemble it"
   ```
4. **Discover commands/options**
   ```bash
   descript-api help
   ```

---

### 10.6 Not Documented / Gaps

- **Published npm version number** — npm page was 403; **Not documented** here.
- **Python or other-language SDK** — **Not documented** (the CLI is Node-only; no SDK is mentioned).
- **Where the API key is stored on disk** (config file path/format) — **Not documented**.
- **Whether the CLI surfaces `publish`, `jobs list/get/delete`, or `projects` commands** that mirror the full REST endpoint set — only `config`, `import`, `agent`, `edit --new`, and `help` are documented. The existence of additional subcommands is **Not documented** (run `descript-api help` to confirm at runtime).
- **Flag for targeting a specific composition** (`composition_id` exists in the raw API) via the CLI — **Not documented**.

## Section 11: Specific Use-Case Evaluation

Each workflow is rated **Fully supported** / **Partial** / **Not supported (or Not documented)** strictly against the verified capabilities in Sections 1–10. The primary mechanism is the agent endpoint (`POST /jobs/agent`) unless noted.

| # | Use case | Verdict | Primary mechanism / why |
|---|---|---|---|
| 1 | Add automatic captions to engine output | ✅ Fully supported | Agent prompt "add captions"; sample response confirms captions added (§1, §15). `POST /jobs/agent` |
| 2 | Apply Studio Sound audio enhancement | ✅ Fully supported | Agent prompt "add studio sound to every clip"; level control ("at about 55%") (§1, §15). `POST /jobs/agent` |
| 3 | Add a thumbnail with title text (agent generates thumbnail?) | ⚠️ App yes / API not documented | In-app Underlord makes styled thumbnail *text* + AI *images* and sets a share-page thumbnail; but **no API thumbnail operation and no documented way to retrieve a finished thumbnail image** (§1.3). Keep thumbnails in-engine for automation |
| 4 | video + transcript + thumbnail-suggestions → polished output (metadata flows into edits) | ⚠️ Partial → ❌ for metadata | Video imports fine and the agent polishes it, but **no transcript/metadata sidecar import is documented** (§2); import auto-transcribes its own. Engine metadata cannot drive Descript edit decisions via API |
| 5 | Stitch MULTIPLE outputs (main + B-roll + intro music) into one project | ⚠️ Partial | Multi-media import → one composition via `add_compositions[].clips[]` is documented (§2, §3). But clips only reference media; **per-clip timing/order/track fields are Not documented** — precise arrangement falls to the agent |
| 6 | Agent decides pacing (slow down / cut quickly) | ✅ Fully supported | "fast-paced highlight reel… <60s", "edit this down to 2 minutes focusing on X" — content-level pacing/selection (§1.2, §1.6) |
| 7 | Auto B-roll from *imported* clips, inserted at relevant moments | ⚠️ Partial | Agent inserts **generated images / stock-library** B-roll at scenes it picks (§1.4). Auto-insertion from *your specific imported clips* at relevant moments is **Not documented** |
| 8 | Apply color grading / a LUT | ❌ Not supported | Open community feature request; no agent or API operation (§1.7) [secondary]. Keep in-engine (cinematic_longform LUT) |
| 9 | End card / title card / lower thirds via agent | ⚠️ Partial | Title/intro cards documented ("add a title card") (§1.1). **Lower-thirds as a named operation: Not documented** |
| 10 | Cinematic cuts (J-cuts, L-cuts, match cuts) | ❌ Not documented | Agent hides jumpcuts with zooms, but J/L/match cuts are **Not documented** as agent capabilities (§1.1) |
| 11 | Generate multiple thumbnail options (A/B) | ⚠️ App maybe / API not documented | Follows from #3 — thumbnail/image generation is an app feature; no API thumbnail operation or asset-retrieval is documented, so A/B isn't an API path |
| 12 | Vertical AND horizontal versions from one source | ⚠️ Partial / Not documented | Social-clip generation is a product-level Underlord capability, but **multi-aspect-ratio output via the API is Not documented** (publish exposes `resolution`, not aspect ratio) (§5). Engine already does 9:16 reframe (shortform) — keep it in-engine |

**Notes per use case.**

1–2 — **Captions & Studio Sound** are the clearest wins: both are first-class documented agent operations with a confirmed sample response, and they are precisely the steps the engine's Phase-7 cut intentionally omits.

3, 11 — **Thumbnails**: in the Descript **app**, Underlord generates styled thumbnail *text* (copyable, not a baked image) and AI *images*, and you can set a project (share-page) thumbnail from a frame/upload/generated image — so the app can do this. The gap is the **API**: no documented thumbnail operation, and crucially **no documented way to pull a finished thumbnail image back out** for upload to YouTube. So for an automated pipeline the engine's local `thumbnail_candidates` + `thumbnail_text_suggestions` remain the right source; thumbnails done in Descript are a manual, in-app step.

4 — **Metadata flow** is the sharpest mismatch: the engine produces a WhisperX `transcript.json` and quotable text, but the import schema has **no field for a transcript or metadata sidecar** (§2). Those artifacts stay useful *inside the engine* (window selection, thumbnail text) but cannot be handed to Descript to steer its edits via API.

5 — **Multi-source assembly** works for *getting media into one composition*; what's missing is fine-grained, frame-accurate arrangement (no per-clip trim/offset fields). For a "main + B-roll + intro music" stitch you import all three and then let the agent arrange ("add this as an intro, place B-roll over the section about X").

6 — **Pacing/selection** is a genuine strength and a reason to delegate: the agent makes editorial keep/cut decisions from longer footage, which the engine's window scorer also does — so this overlaps and you can choose where it happens.

7 — **B-roll**: "generate B-roll for scenes that need it" is supported; "use *my* clip X at the moment I mention Y" is not documented as an automatic behavior.

8 — **Color/LUT**: not available; the engine already does this locally in `cinematic_longform` (ffmpeg `lut3d`), which is the correct division of labor.

9 — **Title cards** yes; **lower-thirds** not named. Try via free-text prompt, but treat as unverified.

10, 12 — **Advanced cuts and multi-aspect output** are not documented at the API level; do not design a pipeline that depends on them without testing.

## Section 12: Agent Prompt Examples

This section catalogs **every example prompt** found across the primary docs, help center, and Descript marketing pages. Where docs do not state credits or duration for a given prompt, those columns read “Not documented.” Across all examples the behavior is the same async contract: submit to `POST /jobs/agent`, receive a `job_id`, poll `GET /jobs/{job_id}` (source: https://docs.descriptapi.com/).

### 12.1 Prompts from the API reference docs (primary)

| # | Exact prompt text | What the agent does (per docs) | Credits shown | Duration / async |
|---|---|---|---|---|
| 1 | `Add studio sound and captions` | Applies Studio Sound + adds captions; sample agent_response: “Done! I’ve applied Studio Sound… added classic karaoke-style captions” | `ai_credits_used: 32` (sample) | Async job; sample run 05:42:27→ 05:43:15 (≈48s) |
| 2 | `create a 30-second video about cooking tips with background music` | Generates a 30s video on cooking tips with background music | Not documented | Async |
| 3 | `Remove filler words and add Studio Sound to all clips` | Removes filler words + applies Studio Sound to all clips | Not documented | Async |
| 4 | `Write a script about how to make great coffee` | Writes a script (script generation) | Not documented | Async |
| 5 | `add studio sound to every clip` | Applies Studio Sound to every clip | Not documented | Async |
| 6 | `remove all filler words from the transcript` | Removes all filler words | Not documented | Async |
| 7 | `create a 30-second highlight reel` | Builds a 30s highlight reel | Not documented | Async |
| 8 | `remove the section from 1:30 to 2:15` | Removes the timecoded section | Not documented | Async |

(source: https://docs.descriptapi.com/; source: https://docs.descriptapi.com/openapi-experimental.html)

The API/landing page also lists these example requests (primary): “Create a Descript project from this video URL,” “Add Studio Sound and captions to my latest project,” “Make a 30-second highlight reel from my project,” “Write a script about how to make great coffee and create a video from it” (source: https://www.descript.com/api).

### 12.2 Prompts from Underlord product page (primary, descript.com/underlord)

| Exact prompt text | What the agent does | Credits | Duration |
|---|---|---|---|
| `This how-to video has good content but bad energy—HELP!` | Re-edits for energy/engagement | Not documented | Async |
| `Hide my jumpcuts by adding zooms` | Adds zooms over jump cuts | Not documented | Async |
| `Edit this down to 2 minutes—focusing on when I talked about Freud` | Topic-focused cut down to 2 min | Not documented | Async |
| `Add camera layouts every time I reference the new dashboard feature` | Inserts layouts at referenced moments | Not documented | Async |
| `Re-edit this announcement video to feel less like 'change' and more like 'progress'` | Tone-driven re-edit | Not documented | Async |
| `Copy all the parts where Laura is talking into a new composition` | Speaker-based scene extraction | Not documented | Async |
| `Make the trailer for this webinar recording` | Generates a trailer | Not documented | Async |

(source: https://www.descript.com/underlord)

### 12.3 Prompts from the Underlord primer blog (primary, descript.com/blog)

| Exact prompt text | What it does |
|---|---|
| `I want to turn this hour-long webinar about our product into a 5-minute YouTube video that just hits on the most compelling points.` | Long-to-short repurposing |
| `Can you take a look at this demo and let me know your thoughts on making it more inspiring?` | Conversational critique/edit suggestion |
| `I want this this video to connect with people and make them feel motivated and supported.` | Tone-goal-driven edit |
| `Turn this video into a clear, narrative-driven product demo that will engage hiring managers on YouTube.` | Audience-targeted re-edit |
| `Make this script sound more natural and conversational, like how a friend would explain it.` | Script rewrite |
| `Make it visually interesting by drawing attention to key moments` | Visual emphasis edits |
| `First, remove retakes—use the last complete take in most cases. Then cut filler words, unless removing them will make it sound unnatural. Then apply Studio sound at about 55%. Then, apply the Berlin Carrot layout to the entire video. Next, add captions to the entire video. Finally, add a title card with a title card that captures the main takeaway in 5 words or less.` | Multi-step ordered workflow (retakes → filler → Studio Sound 55% → layout → captions → title card) |
| `Find scenes that would benefit from B-roll. Use generated images in a vibrant, documentary-inspired style that captures unconventional individuals in candid, emotionally resonant moments.` | Generated-image B-roll placement with style direction |

(source: https://www.descript.com/blog/article/underlord-ai-video-editor-primer)

### 12.4 Prompts from the “effective prompts” help article (primary)

| Exact prompt text | What it does |
|---|---|
| `make this a fast-paced highlight reel for TikTok with a humorous tone, that's less than 60 seconds.` | Fast-paced sub-60s TikTok highlight reel with tone control |
| `highlight each of the five core principles, add animated callouts for each.` | Topic-segmented edit with animated callouts |
| `Take this training recording and repurpose it as an internal training video. Remove the Q&A sections, add chapter markers, and create an intro card showing the title and chapters. Include captions throughout. Keep it clear and instructional — friendly but professional. Final video should be 12–18 minutes.` | Full repurpose: remove Q&A, chapter markers, intro card, captions, tone + target length |

(source: https://help.descript.com/hc/en-us/articles/38217205340813-How-to-write-effective-prompts-for-Descript-s-AI-features; source: https://help.descript.com/hc/en-us/articles/36803785502221-Underlord-beta-Your-AI-co-editor-in-Descript)

**Context attachment (help article):** prompts can be scoped by attaching scenes, script passages, layers, Speakers, or files — via typing `@`, the `@` button, or right-click → “Add to Underlord” (source: https://help.descript.com/hc/en-us/articles/38217205340813-How-to-write-effective-prompts-for-Descript-s-AI-features). Whether the public API exposes `@`-style attachment beyond `composition_id` is **Not documented**.

**Note on credits/duration:** Only example #1 (`Add studio sound and captions`) has a documented credit count (`ai_credits_used: 32`) and timestamps (≈48s) in the sample payload (source: https://docs.descriptapi.com/openapi-experimental.html). For all other prompts, credits and duration are **Not documented**.

## Section 13: Integration Architecture Patterns

Five candidate architectures for an engine that pre-processes locally and hands off to Descript. Costs are qualitative because per-operation dollar rates are not published (see Cost Analysis); credit figures are secondary.

### Pattern A — Local files → manual import to Descript desktop → manual polish
- **Feasibility:** Always works; no API involved. The engine's Phase-7 1080p MP4 drops straight into the desktop app.
- **API cost:** $0 API usage (consumes the plan's media minutes / AI credits only when *you* run features in-app).
- **Latency:** Human-paced; no automated wait.
- **Failure modes:** Manual drag-drop step; no automation; easy to forget steps across many videos.
- **Control vs automation:** Maximum control, zero automation.

### Pattern B — Engine uploads via API → user polishes manually in Descript web/desktop
- **Feasibility:** Fully supported. `POST /jobs/import/project_media` (URL import or 3-step signed-URL upload) creates the project; you open `project_url` and edit by hand (§2).
- **API cost:** Media minutes for the import (≈ the file's duration); **no AI credits** until you invoke AI features.
- **Latency:** Import is async (seconds–minutes for a 5–10 min clip); polishing is human-paced.
- **Failure modes:** Import-URL reachability / Range-request support; 3-hour signed-URL expiry; 402 if media minutes exhausted.
- **Control vs automation:** High control, partial automation (ingest only). Good default for a single creator who wants to keep editorial hands-on.

### Pattern C — Engine uploads via API → ONE-SHOT agent prompt for polish → download (RECOMMENDED target)
- **Feasibility:** Fully supported and idiomatic — Descript explicitly recommends one-shot prompts (§4). Flow: import → `POST /jobs/agent` with a single multi-instruction prompt (e.g., *"Remove filler words, add Studio Sound to all clips, and add captions throughout."*) → poll `GET /jobs/{job_id}` or receive `callback_url` webhook → `POST /jobs/publish` → download from `download_url`.
- **API cost:** Media minutes (import) + AI credits per agent job. Documented sample "Add studio sound and captions" = **32 credits** [primary sample]; a fuller polish prompt ≈ **30–60 credits** [secondary estimate].
- **Latency:** Agent jobs run in the background; the one documented sample completed in **~48 s**. Real multi-op prompts on 5–10 min video will be longer (Not documented — budget minutes, not seconds).
- **Failure modes:** One-shot means a wrong/over-broad prompt yields a wrong edit with no mid-job correction (re-run = more credits); 402 (credits), 403 (agent disabled by drive admin), 429; no webhook signature to verify authenticity (treat callbacks as untrusted, re-fetch `GET /jobs/{job_id}` to confirm).
- **Control vs automation:** Best automation/effort ratio for a personal pipeline; control limited to prompt quality.

### Pattern D — Engine uploads → MULTIPLE sequential agent calls → download final
- **Feasibility:** Supported but **counter to Descript's guidance** (one-shot preferred). Each step is a separate `POST /jobs/agent` job that must complete before the next.
- **API cost:** **N × agent jobs = N × credits** — strictly more than Pattern C for the same result.
- **Latency:** Sum of N async jobs (serialized — each needs the prior `project_changed`), so noticeably slower.
- **Failure modes:** Compounding — any step can 402/timeout; partial-edit states; more polling surface.
- **Control vs automation:** Slightly more granular sequencing, at higher cost/latency. Justify only if a single prompt can't express the order you need.

### Pattern E — Engine as a thin wrapper around Descript (all editing via Descript)
- **Feasibility:** **Not advisable.** The API lacks low-level timeline control, transcript/metadata sidecar import, color grading/LUTs, and multi-aspect output (§3, §4, §8 use-cases). You'd surrender the engine's analysis value (moment scoring, LUT grade, thumbnail selection) for capabilities Descript doesn't expose.
- **API cost:** Highest (everything metered).
- **Latency / failure modes:** Most API surface, most failure points, vendor lock-in to a beta API.
- **Control vs automation:** Maximum automation, minimum control — the wrong trade for a cinematic, owner-tuned pipeline.

## Section 14: Limitations and Gotchas

Consolidated from every "Not documented" / "Not supported" finding across Sections 1–10. Items are grouped; primary unless tagged [secondary].

### "Work in progress" / beta / gated
- **`GET /status`** is present at `https://descriptapi.com/v1/status` but marked **work-in-progress / not yet available** and errors if called (§8).
- **The whole API is in public beta / Early Access** (source: https://www.descript.com/enterprise/api-early-access); expect changes. A known-issues page (`descript.notion.site/descript-apis-known-issues`, referenced from descript.com/api) **could not be retrieved** — its specific caveats are Not documented here.
- **`POST /edit_in_descript/schema` is partner-gated** — requires contacting Descript; a 403 means the Drive lacks Edit-in-Descript permission (§6). Exact partner-approval criteria/SLA/terms are Not documented.

### Editing / composition (the biggest gaps)
- **No low-level timeline editing API.** No documented endpoint to trim, reorder, set transitions, place titles/lower-thirds, mix audio, or set per-clip volume — **all editing is agent-mediated** via `POST /jobs/agent` (§4).
- **No per-clip timing/trim/track/layer fields** in the composition `clips[]` schema; clips only reference a media key (§3). **Multitrack construction via the API is Not documented** (flat `clips` array only).
- **No `GET/POST/PUT /compositions` endpoints**; compositions are created only via `add_compositions` on import and read only as summaries on `GET /projects/{id}` (§3). **No endpoint returns a composition's internal clip/track/layer structure.**
- **Color grading / custom LUTs: Not supported** (open community request) (§1.7) [secondary].
- **Thumbnails via API: Not documented.** In-app Underlord generates thumbnail *text* + AI *images* and you can set a share-page thumbnail, but there is no API thumbnail operation and **no documented way to retrieve a finished thumbnail image** (publish returns video + share-page preview only) — so A/B thumbnail generation is not an API path (§1.3).
- **Named transition primitives, AI music generation, J/L/match cuts, lower-thirds, multi-aspect output: Not documented** (§1, §11).

### Import
- **No transcript sidecar import** (SRT/VTT/JSON) and **no metadata sidecar import** (chapters/markers/thumbnail suggestions) — import auto-transcribes its own (§2).
- **Automatic scene detection on import: Not documented** (auto-transcription is, scene detection isn't) (§2).
- **File-size, duration, resolution, and max-files-per-request limits are Not documented for the API endpoint.** The 50 MB in the example is a sample `file_size`; the 1 GB / 15-hour figures are **app** (Edit-in-Descript) limits, not confirmed API limits [secondary] (§2).
- **URL imports must support HTTP Range requests**; direct-upload signed URLs **expire in 3 hours** (§2).

### Publish / export
- **No render-the-*video*-to-a-file endpoint** — the rendered video is available only via publish (`share_url` + signed `download_url`). *Transcript and timeline DO have direct export endpoints* (`POST /export/transcript`, `POST /export/timeline`) — see §5.7 (§5).
- **No documented codec/container selector** (MP4 vs MOV); only `media_type` (Video/Audio) and `resolution` (§5).
- **Transcript export DOES have a dedicated endpoint** — `POST /export/transcript` returns the transcript file directly (documented examples: plain text with speaker labels, Markdown with timecodes; VTT referenced; **SRT not shown** in the spec). Subtitles are *also* available inline as WEBVTT on `GET /published_projects/{slug}` (§5). *(Correction to an earlier draft that said no transcript-export endpoint exists.)*
- **Signed `download_url` TTL is Not documented** (only the `download_url_expires_at` timestamp is returned). **Password-protected share pages are Not documented as an API parameter** (§5).
- Whether publish enforces **plan-based resolution caps** (e.g., Free = 720p) via error vs silent downgrade is Not documented [secondary].

### Webhooks / async
- **No webhook signature verification / shared-secret mechanism** documented — treat callbacks as unauthenticated and re-fetch `GET /jobs/{job_id}` to confirm (§7).
- **Webhook retry behavior, delivery timeout, required 2xx ack, and delivery/ordering guarantees: Not documented** (§7).
- **Recommended polling interval: Not documented.** A distinct **`queued`** job state is not explicitly documented (only `running` / `stopped`) (§7).
- **`DELETE /jobs/{job_id}` idempotency on already-finished jobs: Not documented.** **No `publish` value** in the `GET /jobs` `type` filter enum (only `import/project_media`, `agent`) (§7).

### Auth / rate limits
- **The global (non-published-project) rate-limit number is Not documented** — only that limiting exists, returning 429 with `Retry-After`, `X-RateLimit-Remaining`, `X-RateLimit-Consumed`. The **1000 req/hr** figure applies **only** to `GET /published_projects/{slug}` (§8). **`X-RateLimit-Limit` / `X-RateLimit-Reset` are Not documented.**
- **A token is scoped to one Drive**; multi-Drive needs multiple tokens [secondary]. **Token revocation/rotation/expiry via API: Not documented.** The token is shown **once** at creation [secondary] (§8).
- **No API method to read credit/media-minute balances or top up** (§8, §9).

### Agent specifics
- **One-shot only** — conversation/follow-up is not practical; Descript recommends a single prompt (§4).
- **Accepted `model` values: Not documented** ("defaults to the default model"). **`@`-style context attachment** (scenes/Speakers/files) beyond `composition_id` is **Not documented for the public API** (§1).
- **Credit cost and latency are documented for only one sample** (32 credits, ~48 s); per-prompt costs/durations otherwise Not documented (§1, §15).

### Tooling / platform
- **CLI is Node.js 24+ only** (`@descript/platform-cli`, binary `descript-api`); **no Python SDK** documented. CLI documents only `config`, `import`, `agent`, `edit --new`, `help` — whether it mirrors `publish` / `jobs` / `projects` is Not documented (§10).
- **Geographic restrictions: none documented** (neither stated nor denied).

## Section 15: Concrete Examples of Agent Power

Each example below pairs a documented prompt with a response payload. Payloads marked **(copied from docs)** are reproduced from Descript’s published examples; those marked **(illustrative — constructed from the documented schema)** are built strictly from the documented `result` field set (`status`, `agent_response`, `project_changed`, `media_seconds_used`, `ai_credits_used`) and are **not** captured live traffic. All values in illustrative payloads except documented constants are placeholders.

The documented result schema for every agent job is: `status`, `agent_response`, `project_changed`, `media_seconds_used`, `ai_credits_used` (source: https://docs.descriptapi.com/openapi-experimental.html).

### 15.1 “Add studio sound and captions”

This is the canonical example shown in the docs (curl + full job-status payload).

**Request (copied from docs):**
```bash
curl -X POST https://descriptapi.com/v1/jobs/agent \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "e2f89ce6",
    "prompt": "Add studio sound and captions"
  }'
```

**Completed job status (copied from docs):**
```json
{
  "job_id": "project-agent-edit-e2f89ce6",
  "job_type": "agent",
  "project_id": "YOUR_PROJECT_ID",
  "project_url": "https://web.descript.com/e2f89ce6",
  "job_state": "stopped",
  "created_at": "2026-02-09T05:42:27.554Z",
  "stopped_at": "2026-02-09T05:43:15.296Z",
  "drive_id": "1df135a5-dc4a-4dc3-8f7d-681cfbe961e4",
  "result": {
    "status": "success",
    "agent_response": "Done! I've applied Studio Sound to enhance your audio quality and added classic karaoke-style captions to your video.",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 32
  }
}
```
(source: https://docs.descriptapi.com/; source: https://docs.descriptapi.com/openapi-experimental.html)

### 15.2 “create a 30-second highlight reel”

Documented prompt (source: https://docs.descriptapi.com/). No published response payload — the following is **(illustrative — constructed from the documented schema)**:
```json
{
  "job_id": "<uuid>",
  "job_type": "agent",
  "project_id": "<project-id>",
  "project_url": "https://web.descript.com/<project-id>",
  "job_state": "stopped",
  "drive_id": "<drive-id>",
  "result": {
    "status": "success",
    "agent_response": "<natural-language summary of the highlight reel created>",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 0
  }
}
```

### 15.3 “remove all filler words from the transcript”

Documented prompt (source: https://docs.descriptapi.com/). **(illustrative — constructed from the documented schema):**
```json
{
  "job_id": "<uuid>",
  "job_type": "agent",
  "job_state": "stopped",
  "result": {
    "status": "success",
    "agent_response": "<summary of filler words removed>",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 0
  }
}
```

### 15.4 “remove the section from 1:30 to 2:15”

Documented prompt (source: https://docs.descriptapi.com/). **(illustrative — constructed from the documented schema):**
```json
{
  "job_id": "<uuid>",
  "job_type": "agent",
  "job_state": "stopped",
  "result": {
    "status": "success",
    "agent_response": "<summary confirming the 1:30–2:15 section was removed>",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 0
  }
}
```

### 15.5 “Remove filler words and add Studio Sound to all clips”

Documented prompt (source: https://docs.descriptapi.com/). **(illustrative — constructed from the documented schema):**
```json
{
  "result": {
    "status": "success",
    "agent_response": "<summary: filler words removed and Studio Sound applied to all clips>",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 0
  }
}
```

### 15.6 “create a 30-second video about cooking tips with background music”

Documented prompt (source: https://docs.descriptapi.com/). This creates a new project from scratch, so the **201 creation** response (copied from docs schema) returns `job_id`, `drive_id`, `project_id`, `project_url`:
```json
{
  "job_id": "6dc3f30a-58c2-4174-96a6-dc18cf3c7776",
  "drive_id": "c9c5c47e-158a-49f7-846b-4f6ee2a229a2",
  "project_id": "9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb",
  "project_url": "https://web.descript.com/9f36ee32-5a2c-47e7-b1a3-94991d3e3ddb"
}
```
(source: https://docs.descriptapi.com/openapi-experimental.html)

The completed-job `result` for this prompt is **(illustrative — constructed from the documented schema)**:
```json
{
  "result": {
    "status": "success",
    "agent_response": "<summary: 30s cooking-tips video generated with background music>",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 0
  }
}
```

### 15.7 “Write a script about how to make great coffee”

Documented prompt (source: https://docs.descriptapi.com/). **(illustrative — constructed from the documented schema):**
```json
{
  "result": {
    "status": "success",
    "agent_response": "<summary of the generated coffee script>",
    "project_changed": true,
    "media_seconds_used": 0,
    "ai_credits_used": 0
  }
}
```

### Notes on payload interpretation
- The only payload Descript publishes in full is §15.1; every other `result` block here is illustrative and uses placeholder values for non-constant fields (source: https://docs.descriptapi.com/openapi-experimental.html).
- `media_seconds_used: 0` appears in the docs sample where the source media was already imported; for prompts that generate or process media the value may differ — the docs do not enumerate per-operation values (Not documented).
- A failed job would set `result.status` to a non-success value and may not set `project_changed: true`; the exact failure payload shape beyond the `status` field is **Not documented**.

## Architecture Recommendation

**Recommendation: target Pattern C (upload via API → one-shot agent polish → publish/download), starting from Pattern B (upload + manual polish) until the polish prompt is trusted.** This fits the engine's design and Descript's actual capabilities.

**Why C fits this engine.** Phase 7 `standard_longform` already emits exactly what Descript wants as input — a clean **1080p 16:9 h264/MP4** cut with **no captions, no Studio Sound, no watermark, no color grade** ("Descript adds all final polish"). That cut maps 1:1 onto the agent's documented strengths: a single prompt like *"Remove filler words, add Studio Sound to every clip, and add captions throughout"* covers the highest-value polish in one metered job, then `POST /jobs/publish` returns a downloadable 1080p file. The engine's existing thread-lane scheduler and the API's `callback_url` webhooks compose cleanly for fire-and-poll batching.

**What to keep IN the engine (do not delegate):**
- **Color grade / LUT** — Descript has no LUT support; `cinematic_longform` already grades locally via ffmpeg `lut3d` at source resolution. Correct as-is.
- **Moment / window selection** — the engine's scored best-window cut is your editorial signature; let it define the input rather than handing raw footage to the agent.
- **Thumbnails** — Descript can't generate thumbnail images via API; keep the engine's `thumbnail_candidates` + `thumbnail_text_suggestions`.
- **9:16 reframe** (shortform) — multi-aspect output isn't documented in the API; the engine's auto-reframe stays.

**What to delegate TO Descript (via one agent prompt):** captions, Studio Sound, filler-word removal, and — when wanted — title cards, B-roll, layouts, and further pacing trims. These are precisely the steps Phase 7 deliberately leaves out.

**Operational guardrails (from §14):** the agent is one-shot, so invest in one well-formed prompt and accept that a re-run costs credits; webhooks are unsigned, so re-fetch `GET /jobs/{job_id}` before trusting a callback; signed `download_url`s and upload URLs expire (3 h for upload), so download promptly; there is no programmatic balance check, so monitor credits manually. Because the API is beta with a referenced-but-unreadable known-issues page, keep Pattern A/B (manual) as a fallback and don't make the engine hard-depend on the agent for a release.

**Net division of labor:** engine = analysis, selection, grade, reframe, thumbnails (the "intelligence"); Descript = caption/sound/filler/B-roll polish (the "finishing"). That is the split the codebase already assumes — the API supports it, with the metadata-handoff caveat that the engine's transcript/thumbnail-text artifacts stay engine-side and can't steer Descript's edits.

## Cost Analysis

**Target volume:** 5–10 videos/week at 5–10 minutes each ≈ **20–43 videos/month** (the engine's Phase-7 default window is 300 s / 5 min).

**Important:** Descript's API docs publish **no per-operation dollar or per-credit rate card**. Billing is a two-resource consumption model — **media minutes** (`media_seconds_used`) and **AI credits** (`ai_credits_used`) — drawn from your plan; dollar conversions below are **[secondary]** (descript.com/pricing and third-party breakdowns) and should be treated as estimates, not quotes.

### Consumption estimate (Pattern C: import + one polish prompt + publish)

| Resource | Per video | Monthly (20 videos) | Monthly (43 videos) | Basis |
|---|---|---|---|---|
| **Media minutes** | ≈ clip duration (5–10 min) on import | ~100–200 min (~1.7–3.3 h) | ~215–430 min (~3.6–7.2 h) | Import meters file duration (§9) |
| **AI credits** | ~32 (captions+Studio Sound sample) to ~30–60 (fuller prompt) | ~600–1,200 | ~1,300–2,600 | 32-credit sample [primary]; fuller-prompt range [secondary] |

The **binding constraint is AI credits, not media minutes** — even the high end (~7 media hours) sits well inside mid-tier plans, while credit demand can exceed a mid-tier monthly grant.

### Plan-tier fit [secondary: descript.com/pricing; cotovan.com; gptprompts.ai]

| Plan | ~Price | Media | AI credits/mo | Verdict for this volume |
|---|---|---|---|---|
| Free | $0 | 60 min, 720p | 100 | ❌ Too little; 720p cap; API-on-Free eligibility unresolved |
| Hobbyist | $16–24/mo | 10 media h, 1080p | 400 | ❌ Credits cover only ~8–13 videos |
| **Creator** | **$24–35/mo** | 30 media h, 4K | 800 (+500 one-time) | ✅ Covers the **low end** (~20 light-prompt videos); heavy months need top-ups |
| **Business** | **$50–65/mo** | 40 media h | 1,500 (+1,000 one-time) | ✅ Best fit for **10 videos/week** or fuller prompts (~30–50 videos) |
| Enterprise | custom | custom | custom | Only if you exceed Business or need SSO/seats |

**Top-ups** (Creator/Business only, roll over ≤12 mo) [secondary]: AI credits 350/$35, 1,000/$80, 4,000/$200; media 5 h/$25, 20 h/$80, 50 h/$150.

### Recommendation
- **5 videos/week, basic polish (captions + Studio Sound + filler):** **Creator (~$24–35/mo)** is sufficient; add a 1,000-credit top-up (~$80) in heavy months.
- **10 videos/week, or fuller prompts (B-roll, layouts, title cards):** **Business (~$50–65/mo)** for the larger credit grant and top-up access.
- API access itself is **included at no extra cost** on paid plans (source: https://www.descript.com/api) — you pay only via plan credits/minutes, not per API call.
- **Resolution note:** the engine outputs 1080p, so the Free/Hobbyist 1080p ceiling is not limiting; you do **not** need Creator's 4K for resolution — only for credits.
- **Assumptions:** one import + one agent job + one publish per video; ~30–60 credits/video; credits don't roll over month-to-month [secondary]; re-runs (one-shot mistakes) add credits. Validate real `ai_credits_used` on your first few jobs before committing to a tier.

## Open Questions

Questions the documentation does not answer — things to **test directly or ask Descript support** before depending on them:

**Pricing & metering**
1. Exact **AI-credit cost per agent prompt type** (only the 32-credit "studio sound + captions" sample is published) and whether import/publish consume credits or only media minutes.
2. Whether **publish/export consumes media minutes** separately from import, and the exact **media-seconds → media-minute** rounding.
3. Whether the **Free plan can mint an API token** (primary says "paying users"; one secondary source lists Free as eligible — unresolved).
4. Any **API method to read remaining credits/media minutes or top up** programmatically (none documented — currently manual).

**Rate limits & reliability**
5. The **global rate-limit number** for import/agent/publish (only the 1000 req/hr published-project limit is given).
6. **Webhook authenticity** (is there a signature/secret?), **retry policy**, timeout, required ack, and **delivery/ordering guarantees**.
7. **Recommended polling interval** for `GET /jobs/{job_id}`, and whether a **`queued`** state exists.
8. **`DELETE /jobs/{job_id}` behavior on terminal jobs** (idempotent? error?).

**Capabilities to confirm by test**
9. **Agent job latency** for realistic 5–10 min multi-op prompts (only one ~48 s sample exists).
10. Whether the agent can **auto-insert B-roll from *my* imported clips** (vs only generated/stock images) at relevant moments.
11. Whether **lower-thirds, named transitions, J/L/match cuts, AI music generation, and multi-aspect (9:16 + 16:9) output** are achievable via free-text prompt despite being undocumented.
12. Whether **`add_compositions[].clips[]`** supports any **per-clip timing/trim/ordering** beyond a flat media reference (for multi-source stitching).
13. Exact **import limits** for the API endpoint (file size, duration, resolution, max files per request) — only app-level limits are documented.

**Publish/export**
14. The **container/codec** of the published file (MP4 vs MOV) and the **signed `download_url` TTL**.
15. Whether publish **enforces plan resolution caps** via error vs silent downgrade, and whether **password-protected** share pages are settable via API.

**Platform / process**
16. Whether the **CLI mirrors `publish`/`jobs`/`projects`** (only `config`/`import`/`agent`/`edit`/`help` are documented) and whether a **Python SDK** is planned.
17. Contents of the **API known-issues page** (`descript.notion.site/descript-apis-known-issues`) — referenced but not retrievable during this research.
18. **Edit-in-Descript partner** approval criteria, SLA, and whether `GET /published_projects/{slug}` needs a partner-entitled token.

## Appendix: Sources & Verification Notes

### How this document was produced
- **Tooling premise corrected:** the request specified Context7, but **Context7 does not index the Descript video API** — its only `descript` entry (`/descript-org/descript`) is an unrelated open-source TypeScript HTTP-request library. Research was therefore conducted by **direct documentation fetches** (WebFetch/WebSearch), not a registry index.
- **Method:** 5 parallel research agents fetched the sources below and produced **148 cited claims** (105 primary / 39 secondary / 4 inferred) and **57 documented gaps**, drafting Sections 1–10, 12, 15. An automated adversarial-verification agent pass was **interrupted mid-run** (its web-tool calls were rejected); verification was completed by **manual review of all 148 claims** for primary-source backing and over-extrapolation, and the derived Sections 0/11/13/14/16/17/18 were synthesized strictly from the verified claim set. Any figure without primary backing is labeled **[secondary]** or **"Not documented."**

### Open-API vs partner-feature conflation (caution for further reading)
Several third-party sources blur the **open standard REST API** (import / agent / publish — available to all paying users) with the **partner-only "Edit in Descript"** feature (`POST /edit_in_descript/schema`, gated). They are distinct; claims about "the Descript API" from secondary sources should be checked against which of the two they describe.

### Primary sources (authoritative)
- `https://docs.descriptapi.com/` — API reference (endpoints, payloads, errors, rate-limit headers)
- `https://docs.descriptapi.com/openapi-experimental.html` — OpenAPI spec (request/response schemas)
- `https://www.descript.com/api` — product/API overview ("if you can prompt it… you can automate it")
- `https://www.descript.com/enterprise/api-early-access` — beta/Early-Access status
- `help.descript.com` articles — Underlord, effective prompts, AI image generation, set & customize thumbnails, MCP, Export & publish, Edit-in-Descript (beta), content-hosting partner, supported file types, layers & clips, subtitles, markers/chapters
- `https://www.descript.com/blog/article/underlord-ai-video-editor-primer`, `https://www.descript.com/underlord` — documented Underlord prompts/capabilities

### Secondary sources (labeled inline; estimates, not quotes)
- `https://www.descript.com/pricing` — plan tiers, credit/media grants, resolution caps
- `https://cotovan.com/post/descript-pricing-media-minutes-ai-credits-topups/` — media-minutes/credits model, top-up pricing
- `https://gptprompts.ai/ai-pricing/descript-pricing` — per-feature AI-credit estimates
- `https://cleanvoice.ai/blog/descript-api-review/`, `https://cleanvoice.ai/blog/descript-api-pricing/` — third-party API review/pricing notes
- `https://www.vp-land.com/p/descript-adds-an-api-and-agentic-automation-to-its-text-based-video-editor` — automation pattern, timeline-handoff exports
- `https://apitracker.io/a/descript` — aggregator (no endpoint/auth specifics)

### Sources referenced but not retrievable during research
- `descript.notion.site/descript-apis-known-issues` (referenced by descript.com/api) — returned empty/blocked
- `help.descript.com` MCP article (46056322186509) and a per-feature credit article (27841674958221) — returned HTTP 403 to direct fetch; captured only via search snippets
- `npmjs.com/package/@descript/platform-cli` — returned HTTP 403 (CLI details taken from docs.descriptapi.com)
