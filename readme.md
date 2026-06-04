# Cinematic Media Engine

Multi-style media intelligence for **kkthedon** — one engine, three content modes
(`standard_longform`, `cinematic_longform`, `shortform`). It ingests a video and
runs an analysis layer that emits per-signal JSON; later phases turn that into
ranked moments, rendered clips, and thumbnails.

> Source of truth is **SPEC.md**. Working state lives in **KNOWLEDGE.md** and **SESSION_LOG.md**.

## Status

- **Phase 1 — Foundation:** ✅ scaffold, config, CLI, ingestion (validate → metadata → audio → workspace).
- **Phase 2 — Analysis Layer:** ✅ `librosa`, `opencv`, `whisper` (WhisperX), `hooks`, `aesthetics`, orchestrated by `run_analysis` and wired into `cme process`.
- **Phase 3 — Intelligence + Scoring:** ✅ per-signal normalization, mode composites (`viral_score`/`cinematic_score`/generic standard) + audio-dynamics `emotional_score`, `pacing_engine`, sliding-window `clustering`, `ranking`, → `moments.json`, orchestrated by `run_intelligence`. (Phase 3.5: window aggregation = 90th percentile.)
- **Phase 4 — Render Layer (shortform):** ✅ `RenderBackend` abstraction + `FFmpegBackend` (`ffmpeg_pipeline`), 9:16 reframe (`social_formats`), burned ASS captions (`subtitles`), orchestrated by `run_render` → playable MP4s in `src/output/shorts/`.
- **Phase 4.5 — Auto-reframe:** ✅ YOLO subject detection (`analysis/subjects`) → content-adaptive 9:16 (`blur_pad` / `track` / `zoom_crop`, `render/auto_reframe`), dual-output A/B.
- **Phase 5 — OpenCLIP + TwelveLabs:** ⏸️ deferred (full scope captured in KNOWLEDGE.md).
- **Phase 6 — Performance/Polish:** ✅ per-artifact analysis caching, parallel thread-lane scheduler (`utils/parallel`), `reprocess` + `workspace list/info/clean`, caffeinate sleep guard, Rich progress.
- **Phase 7 — standard_longform (Descript-prep):** ✅ single best-window 16:9 cut (`render/longform`, no reframe/captions/overlays) + thumbnail frame candidates (`render/thumbnails`) + quotable text suggestions (`intelligence/thumbnail_text`) → `src/output/longform/standard/`.
- **Phase 8 — cinematic_longform (LUT baseline):** ✅ the standard_longform path + a single-stage Sony `lut3d` colour grade (`color_grade` in the mode yaml), cinematic scoring (`cinematic_score`) → `src/output/longform/cinematic/`. All three pipelines now ship.
- **Phase 9 — Descript handoff (`--polish`):** ✅ optional automated polish for the **longform** pipelines (standard + cinematic) — upload the cut to Descript, run ONE combined Underlord prompt (Eye Contact + Studio Sound + Color Adjustments; cinematic also adds music), publish, download to `<base>/polished/` (`src/descript/`). Synchronous, fail-loud. Shortform inherits polish from its source longform, so `--polish` on shortform errors. See [Descript handoff](#descript-handoff---polish).
- **Phase 10 — Professional Color Pipeline (technical correction):** ✅ auto-detect exposure / white-balance / contrast normalization tuned to the cut window, applied **before** the LUT (`color_pipeline` in the mode yaml). Cinematic enabled by default; standard opt-in; shortform N/A. Per-component config override + Phase-6 caching. See [Technical colour correction](#technical-colour-correction-color_pipeline).

## Setup

```bash
uv sync                 # create .venv and install pinned deps (never use pip)
brew install ffmpeg     # system ffmpeg (M1: /opt/homebrew/bin/ffmpeg)
cp .env.example .env    # add TWELVELABS_* keys (only needed in a later phase)
# For --polish (Phase 9): add DESCRIPT_API_KEY to .env (Descript → Settings → API tokens; shown once)
```

> A stray `VIRTUAL_ENV` in the shell profile can pollute `uv`. Prefix commands with
> `env -u VIRTUAL_ENV ...`, or call `.venv/bin/<tool>` directly.

## Usage

```bash
# Ingest + analyze. Writes JSON artifacts into a per-run workspace.
cme process path/to/video.mp4 --mode standard_longform
cme process path/to/video.mp4 --mode shortform           # shortform always transcribes
cme process path/to/video.mp4 --mode cinematic_longform --transcribe   # opt in to transcription
cme process path/to/video.mp4 --mode standard_longform --mode shortform # multiple modes
```

### What it produces

```
workspace/<run-id>/
├── audio.wav                 # 16 kHz mono (ingestion)
├── metadata.json             # ffprobe metadata (incl. source path)
├── transcript.json           # WhisperX transcript + word alignment (only if transcribing)
├── moments.json              # ranked clip windows + per-signal contributions (Phase 3)
└── analysis/
    ├── librosa.json          # RMS energy curve, speech-band intensity, audio peaks
    ├── opencv.json           # motion curve + scene-cut timestamps
    ├── aesthetics.json        # lighting / composition / sharpness / colorfulness (cv2 basics)
    └── hooks.json            # hook-phrase + quotable detections (only if transcribing)
```

**Transcription is mode-conditional:** `shortform` always transcribes; `standard_longform`
and `cinematic_longform` skip it unless you pass `--transcribe`. The WhisperX stack
(faster-whisper on CPU + wav2vec2 alignment on MPS) downloads ~800 MB of models on first use.

**`moments.json` (Phase 3):** ranked 60s clip windows for shortform (the clip length is
locked to 60s for cross-platform monetization). Each moment carries its `composite_score`
and a `signal_contributions` breakdown (weight·normalized-signal, summing to the score) for
tuning. `weights` reports configured-vs-effective weights and any `unavailable_signals`
(redistributed). If no window clears the mode's `score_threshold`, `moments` is empty and
`best_window_score` reports where the source landed — thresholds are never lowered to force
results. Run it with:

```bash
cme process path/to/video.mp4 --mode shortform --no-twelvelabs   # local-only, free
```

### standard_longform (Descript-prep)

`standard_longform` is the "intelligent prep" path for **Descript**: the engine selects the single
strongest window (`pacing.preferred_duration_sec`, default 300 s, clamped to source length) and emits
a clean **16:9 1080p** cut — **no reframe, captions, watermark, or color grade** (Descript adds all
final polish). Window selection is audio-driven (`audio_energy` + `conversational`); `--transcribe`
adds `transcript_quality` and enables thumbnail text suggestions.

```bash
cme process path/to/stream.mp4 --mode standard_longform                # audio-driven window
cme process path/to/stream.mp4 --mode standard_longform --transcribe   # + thumbnail text suggestions
```

Output under `src/output/longform/standard/`:

- `<date>_<source>_standard_longform.mp4` — the 1080p 16:9 cut
- `thumbnails/<source>_thumb_NN.jpg` — top-3 frames by aesthetics composite (composition + lighting + sharpness)
- `moments.json` additionally carries `thumbnail_candidates`, `thumbnail_text_suggestions` (3–7 word
  quotable phrases), and `thumbnail_text_status` (`ok` / `transcript_unavailable` /
  `no_quotable_phrases_found`; `not_generated` for modes that don't extract them). Text suggestions
  require `--transcribe`. Multi-window stitching is Phase 7b.

### cinematic_longform (LUT-graded Descript-prep)

`cinematic_longform` is `standard_longform` **plus one thing**: a single-stage 3D LUT colour grade
applied during the cut. Same best-window selection, 16:9 1080p output, thumbnails, and quotable-text
prep — but scored with the cinematic profile (`emotional_moments` / `lighting_quality` /
`visual_composition` / `pacing_curve`, with `openclip_aesthetic` + `soundtrack_sync` redistributed
until Phase 5) and graded via ffmpeg `lut3d` (LUT applied at source resolution, **before** downscale).

```bash
cme process path/to/footage.mp4 --mode cinematic_longform --transcribe --no-twelvelabs
```

Output under `src/output/longform/cinematic/` (same shape as standard: `<date>_<source>_cinematic_longform.mp4`,
`thumbnails/`, and the moments.json thumbnail fields).

The grade is configured in [`content_modes/cinematic_longform.yaml`](content_modes/cinematic_longform.yaml):

```yaml
color_grade:
  enabled: true
  lut_path: src/branding/luts/creativeStyles/cinematic_default.cube   # Sony Cool 709 (S-Log3 → Rec.709)
  interp: tetrahedral
```

- **The shipped LUT expects S-Log3 / S-Gamut3.Cine source** (match your camera capture). On non-log
  footage the grade still applies but will look unusual — that's expected.
- **Swap the look** by replacing the `.cube` file at `lut_path` (Phase 8 is single-LUT, full-intensity —
  no blend, no two-stage chain, no in-config LUT picker; those are future phases).
- Set `color_grade.enabled: false` to get an ungraded cut (identical to standard_longform).
- If `lut_path` is missing or the file isn't on disk, the render **fails loudly** with the resolved
  path — it never silently skips the grade.
- **Note:** the default LUT lives under `src/branding/luts/` (otherwise gitignored as owner IP). The
  committed `cinematic_default.cube` is a freely-distributed Sony NewLook LUT; your own LUTs there stay
  untracked. To use cinematic mode without it, point `lut_path` at any `.cube` you own.

#### Technical colour correction (`color_pipeline`) — Phase 10

Before the LUT, cinematic_longform runs an auto-detected **technical correction** stage
(exposure normalize, white-balance neutralize, contrast standardize) tuned to the selected
cut window. LUTs assume normalized input, so it runs **first** — the `-vf` chain is
`correction,lut3d,scale`. Configured per mode under `color_pipeline:`:

```yaml
color_pipeline:
  technical_correction:
    enabled: true            # cinematic_longform: ON (LUT needs normalized input)
    # exposure:      { mode: override, override_value: 0.0 }    # -1.0 .. 1.0
    # white_balance: { mode: override, override_kelvin: 5600 }  # 2000 .. 10000
    # contrast:      { mode: override, override_curve: medium } # soft | medium | high
```

- **cinematic_longform: enabled by default** — auto-detect (gray-world WB, mid-gray exposure,
  percentile contrast). Override any single component via the commented lines.
- **standard_longform: OFF by default** (Descript's Color Adjustments own colour). Set
  `technical_correction.enabled: true` to opt in.
- **shortform: N/A** (inherits colour from its source longform).
- The computed filter is cached at `workspace/<run>/analysis/color_correction.json` (keyed by
  source + window + config). An unreadable source **fails loudly** — no silent skip.

### Descript handoff (`--polish`)

`--polish` hands a rendered **longform cut** to **Descript** for automated polish via the Underlord
agent API: it uploads the cut to a new Descript project (placing it on a composition timeline), runs
**one combined prompt** (Eye Contact correction + Studio Sound + Color Adjustments; cinematic also
imports a background-music bed — **no captions: captions are shortform-only**, burned by the engine),
publishes, and downloads to `<base>/polished/`. Synchronous and fail-loud — any failed step halts and leaves the Descript project
intact for manual review.

**Polish is longform-only.** Shortform clips are cut FROM polished longform output and inherit polish
through the cut, so polishing them again is redundant paid work — `--polish` on shortform errors with
guidance. The workflow is: **polish a longform cut first, then run shortform mode on the polished
longform as the source.**

```bash
cme process stream.mp4 --mode standard_longform --polish                  # polish the single cut
cme process footage.mp4 --mode cinematic_longform --transcribe --polish   # + imported music bed
cme reprocess workspace/<run-id> --mode standard_longform --polish        # from a cached workspace
# then cut shortform FROM the polished longform output:
cme process src/output/longform/standard/polished/<name>_polished.mp4 --mode shortform
```

Requires `DESCRIPT_API_KEY` in `.env`. Each Underlord job consumes Descript AI credits (~14/video in
testing: Eye Contact 10 + Studio Sound + Color Adjustments; `ai_credits_used` is logged per run). Notes:

- **Cinematic music is user-provided:** drop your bed at `src/branding/music/cinematic_bed.mp3` (path
  set in the mode yaml's `descript.music_path`) before `--polish` on cinematic — the run fails loudly
  before any upload if it's missing.
- **Colour:** the engine's Sony LUT (cinematic) stays baked in; Descript "Color Adjustments"
  (exposure/contrast/white-balance) are applied on top via the prompt (confirmed Underlord-accessible
  at live test). Descript has no LUT/grading operation, so the engine keeps owning the LUT.
- Per-mode config lives in each longform `content_modes/*.yaml` under `descript:` (`enabled`, `prompt`,
  `publish_resolution`, `music_path`). A disabled mode (and all shortform) skips `--polish` with a notice/error.

## Troubleshooting

**Alignment crash on long audio** — `NotImplementedError: Output channels > 65536 not
supported at the MPS device`. WhisperX's wav2vec2 word-alignment runs on MPS by default,
and its conv1d can exceed an MPS op limit on long clips. The engine **auto-detects this and
falls back to CPU** for alignment (logged as a warning) — no action needed. To force the
alignment device explicitly, set `CME_ALIGNMENT_DEVICE` (`auto` | `mps` | `cuda` | `cpu`):

```bash
CME_ALIGNMENT_DEVICE=cpu  cme process video.mp4 --mode shortform   # always CPU (slower, never hits the MPS limit)
CME_ALIGNMENT_DEVICE=auto cme process video.mp4 --mode shortform   # default: MPS if available, else CPU, CPU-fallback on failure
```

(faster-whisper transcription is always CPU on M1 — CTranslate2 has no Metal backend; only
the wav2vec2 alignment step uses the GPU.)

## Development

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest        # full suite (incl. integration decodes)
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not integration and not slow"   # fast subset
env -u VIRTUAL_ENV .venv/bin/python -m mypy src/     # strict, must be clean
env -u VIRTUAL_ENV .venv/bin/python -m ruff check src/ tests/

# The real WhisperX model path is opt-in (skipped by default):
CME_RUN_WHISPER_MODEL=1 env -u VIRTUAL_ENV .venv/bin/python -m pytest -k real_model
```

Tests are written first (TDD). Fixtures are synthetic and generated on demand by
`tests/conftest.py` (a 60 s 1080p test pattern with a 440 Hz tone) — never committed.
Pre-commit runs ruff + mypy + pytest; do not bypass with `--no-verify`.
