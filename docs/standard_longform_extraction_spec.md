# Standard Longform — Extraction & Rebuild Spec

**Purpose:** extract the **Standard Longform** pipeline out of the Cinematic Media Engine into
its own standalone project/library, to be embedded as a feature in another project — keeping
**all** Standard Longform functionality, **excluding** shortform clipping and cinematic grading.

**Generated:** 2026-07-02 · **Source repo:** `Longformcontentproject` (Cinematic Media Engine) ·
**Method:** read-only forensic audit (cross-verified, file:line evidence). Line numbers are
indicative and may drift; function/file names are stable.

> ⚠️ Read §8 before rebuilding. Standard Longform, as it exists today, is a **single-best-window
> extractor**, not a multi-segment assembler — and ~45% of its scoring model is inert. The
> extraction is the moment to decide whether to carry those limitations forward or fix them.

---

## 0. Scope

| Keep (Standard Longform) | Exclude |
|---|---|
| Ingestion, analysis (audio/scene/aesthetic), scoring+selection, single 16:9 cut render, thumbnails, thumbnail-text | **Shortform clipping** (9:16 reframe, captions, dual-output) |
| Optional transcription (`--transcribe`) | **Cinematic grading** (LUT / color pipeline / correction) |
| Workspace/cache model, CLI process/reprocess | **`cme grade`** standalone master tool (ProRes / `--codec` / `--lut` / grade report) |
| | **Subject/YOLO detection** (runs today but Standard never reads it) |
| Descript polish handoff — **decision, see §8.D** | **TwelveLabs** (no implementation exists) |

---

## 1. Canonical pipeline (what it actually does)

```
Raw video
  → ingest()                 validate, extract audio.wav, create workspace, write metadata + cache manifest
  → run_analysis()           librosa.json [REQUIRED] + opencv.json (scene cuts) + aesthetics.json
                             (whisper/hooks only with --transcribe; subjects runs but is DISCARDED)
  → run_intelligence()       score_standard → slide_windows → select_diverse → select_moments → moments.json
  → run_longform_render()    select_best_window() → ONE window → scale to 1080p → H.264 cut
                             + 3 aesthetic thumbnails + text suggestions written back to moments.json
  → output: src/output/longform/standard/<date>_<source>_standard_longform.mp4
  (→ optional Descript --polish; excluded/decision)
```

Inter-layer contract is **JSON files in a workspace dir** (there is no database).

---

## 2. Extraction manifest — files to COPY (with strip notes)

`CADD` = copy as-is · `CSTRIP` = copy but strip excluded code · `DROP` = do not carry over.

### Ingestion (`src/ingestion/`) — all CADD
| File | Notes |
|---|---|
| `__init__.py` (`ingest`) | CADD |
| `validator.py` | CADD (input validation) |
| `audio_extract.py` | CADD (`audio.wav` via ffmpeg) |
| `metadata.py` | CADD (ffprobe metadata model) |
| `workspace.py` | CADD (run dirs, cache manifest, fingerprints, discovery) |

### Analysis (`src/analysis/`)
| File | Action | Notes |
|---|---|---|
| `__init__.py` (`run_analysis`, `_build_stages`, `should_transcribe`, `AnalysisOutputs`) | CSTRIP | remove the `subjects` stage; keep `whisper`/`hooks` **only if** carrying transcription |
| `librosa_analysis.py` | CADD | **required backbone** (audio energy, speech intensity, peaks) |
| `opencv.py` | CADD | scene cuts (PySceneDetect) + motion; Standard uses the scene cuts for boundary snapping |
| `aesthetics.py` | CADD | **cv2-only** frame metrics for thumbnail selection (no torch/CLIP) |
| `whisper.py` | CADD *(optional)* | only if keeping `--transcribe`; pulls `whisperx`/`faster-whisper`/`torch`/`torchaudio` |
| `hooks.py` | CADD *(optional)* | only meaningful with transcription (needs `transcript.json`) |
| `subjects.py` | **DROP** | YOLO person detection — clipping-only; Standard never reads `subjects.json` |

### Intelligence (`src/intelligence/`)
| File | Action | Notes |
|---|---|---|
| `__init__.py` (`run_intelligence`, `_load_signal_data`) | CSTRIP | keep only the `score_standard` branch of the `_SCORERS` dispatch |
| `scoring.py` (`score_standard`, `redistribute_weights`, `normalized_curve`, `coverage_mask`, `make_grid`, `aggregate_window`, `ScoredTimeline`, `SignalData`) | CADD | the standard scorer + shared primitives |
| `clustering.py` (`slide_windows`, `select_diverse`, `_window_slice`) | CADD | window generation + diversity |
| `ranking.py` (`select_moments`, `to_best_window`, `build_moments_result`, `write_moments`, `Moment`, `BestWindow`, `MomentsResult`, `PacingPlan?`) | CADD | selection + the moments.json schema/IO |
| `pacing_engine.py` (`build_pacing_plan`, `PacingPlan`) | CADD | config → window/threshold params |
| `thumbnail_text.py` (`extract_thumbnail_text`, `ThumbnailText`) | CADD | quotable text suggestions (degrades cleanly with no transcript) |
| `cinematic_score.py` | **DROP** | cinematic-only scorer |
| `viral_score.py` | **DROP** | shortform-only scorer |
| `emotional_score.py` | **DROP** | used by shortform/cinematic only |

### Render (`src/render/`)
| File | Action | Notes |
|---|---|---|
| `longform.py` | **CSTRIP (biggest job)** | **KEEP:** `run_longform_render`, `build_longform_command`, `LongformBackend`, `LongformClipSpec`, `select_best_window`, `_resolve_correction`(→ can inline to `""`), `_thumbnail_candidates`, `_thumbnail_text`, `GRADE_FULL_FILE_SEC`? (no). **STRIP:** `run_grade`, `build_grade_command`, `resolve_lut_grade`, `_validate_cube`, `LutGrade`, and the `lut`/`correction` params on `LongformClipSpec`/`build_longform_command` (Standard is always `lut=None`, `correction=""`). |
| `backend.py` (`RenderFormat`) | CADD | shared format DTO |
| `thumbnails.py` (`select_thumbnail_frames`, `extract_thumbnails`) | CADD | thumbnail export |
| `color_pipeline.py` | **DROP** | cinematic correction |
| `grade_report.py` | **DROP** | `cme grade` sidecar |
| `__init__.py` (`run_render`) | **DROP** | shortform orchestrator |
| `ffmpeg_pipeline.py`, `social_formats.py`, `subtitles.py`, `auto_reframe.py` | **DROP** | shortform clipping stack |
| `effects.py`, `logo_animation.py`, `transitions.py` | **DROP** | dead stubs |

### Cross-cutting
| File | Action | Notes |
|---|---|---|
| `config.py` | CSTRIP | drop `ColorPipelineConfig`/grade schemas + shortform-only sub-schemas; keep `Config`, `ModeConfig`, loaders, `PerformanceConfig`, `OpenCVConfig`, `AudioConfig`, `AestheticsConfig`, `ScoringConfig`, `OutputConfig`, `WorkspaceConfig`, `LoggingConfig`, `FFmpegConfig`, `TranscriptionConfig` |
| `utils/ffmpeg.py`, `utils/fileio.py`, `utils/logging.py`, `utils/parallel.py`, `utils/progress.py`, `utils/keepawake.py`, `utils/timestamps.py` | CADD | ffmpeg/ffprobe, JSON IO, structured logging, thread-lane runner, Rich progress, caffeinate guard |
| `cli.py` | CSTRIP | keep `process`/`reprocess` (standard branch) + `workspace` subcommands; drop the `grade` command and the shortform/cinematic branches |
| `descript/` | DECISION (§8.D) | Standard's intended polish handoff; include (pulls `requests`) or drop |

---

## 3. Verified dependency map (for the new `pyproject.toml`)

Confirmed by import grep, **not** by the old pyproject (which lists several unused deps).

**Required (core Standard, no transcription, no Descript):**
```
librosa>=0.10,<0.12      # audio energy / speech intensity (REQUIRED analyzer)
opencv-python>=4.10,<5.0 # scene sampling, aesthetics metrics, thumbnail frames
scenedetect>=0.6,<0.7    # scene cuts (opencv.py)
numpy>=1.26              # scoring/curve math
typer, pydantic, pydantic-settings, pyyaml, python-dotenv, rich
# + system ffmpeg/ffprobe binary
```

**Optional — only if keeping `--transcribe`:**
```
whisperx>=3.0 ; faster-whisper>=1.0,<2.0 ; torch>=2.2,<2.6 ; torchaudio>=2.2,<2.6
```

**Optional — only if keeping Descript polish:** `requests>=2.32,<3.0`

**DROP entirely (verified unused by Standard):**
- `ultralytics` (YOLO/subjects — clipping only)
- `torchvision` (nothing Standard imports it)
- `open-clip-torch` — **not imported anywhere in `src/`** (dead dep; aesthetics is cv2-only)
- `scipy` — **no `import scipy` anywhere** (dead dep)
- `Pillow` — **no `import PIL` anywhere** (dead dep)
- `twelvelabs` — no integration code exists (see §8.B)

> Net: dropping clipping + cinematic + the dead deps removes the **entire torch/ML stack**
> (torch, torchaudio, torchvision, ultralytics, open-clip-torch) unless you keep transcription —
> turning a ~15-dep ML project into a ~10-dep audio/ffmpeg project.

---

## 4. Configuration for the standalone project

- Keep `config.yaml` sections: `performance`, `audio`, `opencv`, `aesthetics`, `scoring`,
  `output`, `ffmpeg`, `workspace`, `logging`. Drop: `subjects`, `openclip`, `reframe_decision`,
  `zoom_crop_decision`, `hooks` (unless transcription), `thumbnails` (shortform), `api.twelvelabs`.
- Keep a single mode config (`standard_longform.yaml`) — or promote it to the project's default
  config since there's now only one mode. **Delete the inert keys** (see §8.B/§8.E):
  - Scoring: remove `pacing_consistency` (0.25) and `narrative_continuity` (0.20) weights
    (TwelveLabs-dependent, always redistributed away) — renormalize the remaining weights.
  - Pacing: remove `min_segment_sec`, `max_segment_sec`, `preserve_chronology`,
    `dead_air_threshold_sec`, `fade_padding_sec` (no consumers) **unless** you implement §8.A.
  - Rendering: remove `transitions`, `effects_intensity`, `branding_intensity`, `branding.*` (no consumers).

Actively-used keys to preserve: `mode_name`, `transcription.{enabled,opt_in_flag,gates_srt}`,
`scoring_weights` (surviving signals), `pacing.preferred_duration_sec`,
`rendering.{resolution,fps,video_codec,bitrate}`, `output.{base_dir,filename_pattern,thumbnail_count,thumbnail_text_count}`,
and the `config.yaml` sections above.

---

## 5. Data model (workspace contract — there is NO database)

Persistence = JSON files under `workspace/<run-id>/`. Producer → consumer pairs:

| Artifact | Producer | Consumer |
|---|---|---|
| `metadata.json`, `audio.wav`, `cache_manifest.json` | ingestion | analysis (cache), intelligence (duration) |
| `analysis/librosa.json` | `librosa_analysis.write_librosa_json` | intelligence `_load_signal_data` |
| `analysis/opencv.json` | `opencv.write_opencv_json` | intelligence (scene cuts → `select_moments`) |
| `analysis/aesthetics.json` | `aesthetics.write_aesthetics_json` | render `_thumbnail_candidates` |
| `transcript.json` *(optional)* | `whisper.write_transcript_json` | intelligence (`transcript_quality`), render `_thumbnail_text` |
| `analysis/hooks.json` *(optional)* | `hooks.write_hooks_json` | intelligence |
| `moments.json` | intelligence `write_moments` | render (reads `select_best_window`; writes back thumbnail fields) |

If embedding as a feature elsewhere, this JSON contract is your integration surface — the host
can call `run_analysis`/`run_intelligence`/`run_longform_render` directly, or read the artifacts.

---

## 6. Function-preservation checklist (call hierarchy)

```
process/reprocess (cli)
 ├─ ingest → validate_input, extract_audio, create_workspace, write_metadata, {read,write}_cache_manifest
 ├─ run_analysis → _build_stages → run_graph(ThreadPool; light/heavy lanes)
 │     analyze_audio→write_librosa_json [REQ]; analyze_video→write_opencv_json; analyze_aesthetics→write_aesthetics_json
 │     (optional) transcribe→write_transcript_json; analyze_hooks→write_hooks_json
 ├─ run_intelligence
 │     _load_signal_data → SignalData
 │     score_standard → ScoredTimeline           # audio_energy + conversational (+ transcript_quality if --transcribe)
 │     build_pacing_plan → PacingPlan
 │     slide_windows → select_diverse → select_moments → build_moments_result → write_moments
 └─ run_longform_render
       select_best_window → moments[0]            # ← single window (see §8.A)
       LongformClipSpec(lut=None, correction="")  # after strip: no lut/correction fields at all
       LongformBackend.render_clip → build_longform_command → run_ffmpeg   # vf="scale=WxH", videotoolbox→libx264 fallback
       _thumbnail_candidates (aesthetics.json) → extract_thumbnails
       _thumbnail_text (transcript?) → write_moments (annotate)
```

Carry over the corresponding tests: `tests/test_render.py` (the `build_longform_command` /
`run_longform_render` / `select_best_window` cases), `tests/test_scoring.py`,
`tests/test_analysis.py` (librosa/opencv/aesthetics), `tests/test_ingestion.py`,
`tests/test_parallel.py`, `tests/conftest.py` + fixtures. **Drop** grade/color/shortform tests
(`test_grade_report.py`, `test_color_pipeline.py`, the shortform/reframe/caption cases in
`test_render.py`, the `grade` cases in `test_cli.py`).

---

## 7. External tools/services

| Tool | Keep? | Where |
|---|---|---|
| FFmpeg/ffprobe (system binary) | ✅ required | audio extract, the cut, thumbnails, probes |
| librosa | ✅ required | audio analyzer |
| PySceneDetect | ✅ required | scene cuts |
| OpenCV | ✅ required | scene sampling, aesthetics, thumbnails |
| WhisperX (+torch) | ⚠️ optional | only with `--transcribe` |
| YOLO/ultralytics | ❌ drop | clipping-only |
| OpenCLIP / TwelveLabs / OCR | ❌ none | not used by Standard (CLIP/OCR absent from code; TwelveLabs unimplemented) |
| Descript REST (requests) | ⚠️ decision | polish handoff (§8.D) |

---

## 8. Architectural issues to DECIDE/FIX during extraction

**A. No multi-window assembly (biggest one).** `render/longform.py::select_best_window` returns
`moments[0]` — Standard renders a single ~5-min window and discards all other ranked moments.
"Multi-window stitching is Phase 7b" is unbuilt. **Decision:** ship the single-window extractor
as-is, or build the real assembler (concat selected moments, honor `preserve_chronology` /
`dead_air_threshold_sec` / `fade_padding_sec`, add transitions). This defines whether the new
project is a "best-clip picker" or a "long-form editor."

**B. ~45% of scoring is inert.** `pacing_consistency` (0.25) + `narrative_continuity` (0.20)
require TwelveLabs, which has **zero integration** (`scoring.py` comments confirm; only config +
a `--no-twelvelabs` stub exist). They're redistributed away every run. **Decision:** delete these
weights (and renormalize) for an honest model, or implement the semantic signals. Today Standard
selection ≈ "loudest/most-talky window, snapped to a scene cut."

**C. Subjects (YOLO) runs but is discarded.** Standard executes YOLO in the heavy lane, then the
render never reads `subjects.json`. **Fix:** drop `subjects.py` from the analysis stage list —
pure win (removes the torch/ultralytics dependency and heavy-lane memory cost).

**D. Descript ownership.** Standard's original reason for existing is "Descript-prep — no grade,
Descript owns polish." **Decision:** (a) include `descript/handoff.py` + `requests` so the feature
ships end-to-end, or (b) drop it and expose the ungraded 1080p cut + thumbnail-text suggestions as
the feature's output for the host project to polish however it wants.

**E. Overloaded module / boundary smell.** `render/longform.py` currently hosts three concerns
(standard cut, cinematic grade, `cme grade` master). After CSTRIP it should host only the standard
cut. Also `select_best_window` lives in **render** but is a **selection** decision — consider moving
it into `intelligence/ranking.py` so render just consumes "the moment to cut."

**F. Output is 1080p H.264, not native.** `build_longform_command` always `scale`s to
`rendering.resolution` (1920×1080) and encodes H.264 (videotoolbox→libx264 fallback). If the host
wants a higher-fidelity master, change `rendering.resolution`/codec (note: unlike `cme grade`,
this path has no ProRes option — that lived in the excluded grade tool).

---

## 9. Suggested standalone layout

```
standard_longform/
  ingestion/    (validator, audio_extract, metadata, workspace, __init__:ingest)
  analysis/     (__init__:run_analysis, librosa_analysis, opencv, aesthetics, [whisper, hooks])
  intelligence/ (__init__:run_intelligence, scoring, clustering, ranking, pacing_engine, thumbnail_text)
  render/       (longform:run_longform_render+build_longform_command+LongformBackend+_thumbnail_*, backend, thumbnails)
  utils/        (ffmpeg, fileio, logging, parallel, progress, keepawake, timestamps)
  config.py     (Config, ModeConfig(or single StandardConfig), loaders)
  cli.py        (process, reprocess, workspace)   # or expose run_* as a library API for the host project
  config.yaml   +  standard.yaml
  tests/        (carried-over subset)
```

If it's a **feature inside another project** (not a CLI), skip `cli.py` and expose a small facade:
`process(source, config) -> Path` wrapping ingest → analyze → intelligence → render.

---

## 10. Final classification (preserve list)

| Category | Items |
|---|---|
| **Required** | ingestion (all); analysis: librosa + opencv + aesthetics + orchestrator; intelligence: score_standard + clustering + ranking + pacing_engine + thumbnail_text; render: run_longform_render + build_longform_command + LongformBackend + thumbnails + RenderFormat; config + utils; CLI process/reprocess (or library facade) |
| **Optional** | transcription (`whisper`+`hooks`, `--transcribe`) → adds `transcript_quality` signal + real thumbnail text; Descript polish (`--polish`) |
| **Legacy / other-mode (leave behind)** | `cme grade` tool (run_grade/build_grade_command/grade_report/color_pipeline/_validate_cube/LutGrade/ProRes/`--codec`/`--lut`); cinematic LUT + scorer; shortform stack (render/__init__, ffmpeg_pipeline, social_formats, subtitles, auto_reframe, viral/emotional scorers); subjects/YOLO |
| **Dead code / dead deps** | effects.py, logo_animation.py, transitions.py, main.py; deps: scipy, Pillow, open-clip-torch, twelvelabs (+ its 2 scoring weights), torchvision; ~10 inert YAML keys |

**To maintain 100% of today's Standard Longform behavior, preserve the "Required" row (+ any
"Optional" you want).** Note that "today's behavior" = single-window 1080p H.264 extraction with
audio-driven selection — §8.A/§8.B are where you decide if the extracted feature should be *more*
than that.

---

## 11. Tools, binaries, models & runtime (host-machine requirements)

This is the operational stack — the things beyond `pip`/`uv` packages that must exist on the
machine running the feature. Tiered: **Required** (core), **Optional** (feature toggles),
**Excluded** (do not carry over), plus a **portability** warning.

### 11.1 System binaries — REQUIRED
| Tool | Why | How used | Notes |
|---|---|---|---|
| **ffmpeg** | audio extraction, the 16:9 cut, thumbnail frame export | `subprocess` via `utils/ffmpeg.py` (`run_ffmpeg`) | must be built with **libx264** (the software encoder + the videotoolbox fallback target); macOS builds also expose `h264_videotoolbox` |
| **ffprobe** | media metadata (duration/streams/dims) | `subprocess` via `utils/ffmpeg.py` (`ffprobe_json`) | ships with ffmpeg |

> These are **not** Python packages — install via the OS (`brew install ffmpeg`, `apt install ffmpeg`, or a static build) and ensure they're on `PATH`. Everything else degrades, but no-ffmpeg = no pipeline.

### 11.2 Runtime & package tooling — REQUIRED
| Tool | Version | Role |
|---|---|---|
| **Python** | 3.11–3.12 (`requires-python >=3.11,<3.13`) | interpreter |
| **uv** | current | dependency + venv manager (project convention: **never pip/poetry**) |

### 11.3 Dev / quality toolchain (carry the gates)
| Tool | Role |
|---|---|
| **ruff** (lint + format) | `ruff check` + `ruff format` (line-length 100, double quotes) |
| **mypy** (strict) | type-check `src/` (strict mode; per-lib `ignore_missing_imports` overrides) |
| **pytest** (+ pytest-cov) | test suite; markers `integration`/`slow`/`gpu` |
| **pre-commit** | hooks: `ruff`, `ruff-format`, `mypy`, a local `pytest` hook, + trailing-whitespace / end-of-file-fixer / check-yaml / check-toml / check-merge-conflict / check-added-large-files / detect-private-key / mixed-line-ending |

### 11.4 ML models & accelerators — OPTIONAL (only if you keep `--transcribe`)
| Item | Why | Notes |
|---|---|---|
| **faster-whisper `small`** (CTranslate2) | transcription backbone | auto-downloads on first run; **no Hugging Face token** needed (diarization/pyannote is NOT used) |
| **wav2vec2 alignment model** (via WhisperX/torchaudio) | word-level timestamps (<100ms) | auto-downloads; runs on **MPS** (Apple Silicon) or CPU |
| **PyTorch (torch/torchaudio)** | hosts the above | the ONLY reason the torch stack exists in this feature |

If you drop transcription (§8), **none of this is needed** — no torch, no model downloads.

### 11.5 Hardware acceleration — OPTIONAL / platform-specific (never assume)
| Accelerator | Platform | Used by | Fallback |
|---|---|---|---|
| **VideoToolbox** (`h264_videotoolbox`) | macOS only | the render encoder | **libx264** (software) — `LongformBackend` already falls back automatically on encoder failure |
| **Metal / MPS** | Apple Silicon | wav2vec2 alignment (transcription only) | **CPU** |

The Whisper (CTranslate2) step is **CPU-only on M1** regardless (no Metal backend) — that's expected, not a bug.

### 11.6 Secrets / env (`.env`)
- **Core Standard Longform: none required.** No API keys for ingest → analyze → intelligence → render.
- **Only if you keep Descript polish (§8.D):** a Descript API token env var (Descript REST via `requests`). Load via `python-dotenv`; never commit `.env`.

### 11.7 EXCLUDED tools — do NOT carry into the new project
| Tool / asset | Belonged to |
|---|---|
| **YOLO weights** (`yolov8n.pt`) + `ultralytics` | subject detection (clipping) |
| **OpenCLIP models** + `open-clip-torch` | never actually imported (dead) |
| **TwelveLabs SDK + API key + index id** | unimplemented "video understanding" |
| **Descript API** (if you choose §8.D "drop") | polish handoff |
| **Brand assets** (LUT `.cube` files, fonts, logos, music beds under `src/branding/`) | cinematic grade + shortform captions/watermark |
| **OCR / tesseract** | not present anywhere (was never in this system) |

### 11.8 ⚠️ Portability warning (you're embedding this elsewhere)
This code is written for **macOS / Apple Silicon (M1)**:
- The encoder default is `h264_videotoolbox` (macOS). On **Linux/cloud**, VideoToolbox does not
  exist — you'll run on **libx264** (already the fallback), so make sure the host's ffmpeg has
  libx264 and consider making libx264 the **default** encoder off-Mac.
- `utils/keepawake.py` uses macOS **caffeinate** — it's a no-op elsewhere but confirm it degrades
  cleanly in the host environment.
- MPS (Apple Silicon) is transcription-only and CPU-falls-back; irrelevant if you drop `--transcribe`.
- The engine was tuned for an **8 GB** machine (thread-lane caps, heavy-lane = 1). Re-tune
  `performance.{max_workers,max_heavy_workers}` for the host's memory.

**Minimum viable host (core, no transcription, no Descript):** Python 3.11–3.12 + uv + ffmpeg
(with libx264) + ffprobe. That's the entire external footprint.

---

## 12. Exact file-copy checklist (verified against disk)

Every `.py` in `src/` is classified below. `✅ COPY` = take as-is · `✂️ COPY+STRIP` = take then delete
the marked code · `🔁 OPTIONAL` = only for that toggle · `❌ SKIP` = do not bring over.

### ✅ COPY AS-IS (core — no edits)
```
src/__init__.py
src/ingestion/__init__.py
src/ingestion/audio_extract.py
src/ingestion/metadata.py
src/ingestion/validator.py
src/ingestion/workspace.py
src/analysis/librosa_analysis.py
src/analysis/opencv.py
src/analysis/aesthetics.py
src/intelligence/scoring.py
src/intelligence/clustering.py
src/intelligence/ranking.py
src/intelligence/pacing_engine.py
src/intelligence/thumbnail_text.py
src/render/backend.py
src/render/thumbnails.py
src/utils/__init__.py
src/utils/ffmpeg.py
src/utils/fileio.py
src/utils/logging.py
src/utils/parallel.py
src/utils/progress.py
src/utils/keepawake.py
src/utils/timestamps.py
```

### ✂️ COPY + STRIP (take, then remove excluded code)
```
src/cli.py                 → keep `process`/`reprocess` (standard branch) + `workspace`; DELETE the `grade` command and shortform/cinematic branches
src/config.py              → DELETE color/grade + shortform-only schemas; keep Config/ModeConfig/loaders/Performance/OpenCV/Audio/Aesthetics/Scoring/Output/Workspace/Logging/FFmpeg/Transcription
src/analysis/__init__.py   → DELETE the `subjects` stage; make `whisper`/`hooks` optional (transcription)
src/intelligence/__init__.py → keep ONLY the `score_standard` branch of the `_SCORERS` dispatch
src/render/longform.py     → KEEP run_longform_render, build_longform_command, LongformBackend, LongformClipSpec, select_best_window, _thumbnail_candidates, _thumbnail_text, _resolve_correction(→inline "").
                             DELETE run_grade, build_grade_command, resolve_lut_grade, _validate_cube, LutGrade, GRADE_FULL_FILE_SEC, and the lut/correction fields+params.
```

### 🔁 OPTIONAL (copy only if you keep the toggle)
```
--transcribe:  src/analysis/whisper.py , src/analysis/hooks.py
Descript (§8.D): src/descript/__init__.py , src/descript/auth.py , src/descript/handoff.py
```

### ❌ SKIP — do NOT copy
```
# cinematic / grade
src/render/color_pipeline.py
src/render/grade_report.py
src/intelligence/cinematic_score.py
# shortform clipping
src/render/__init__.py          ← current one IS the shortform orchestrator; make a NEW empty __init__.py (see gotcha #1)
src/render/auto_reframe.py
src/render/ffmpeg_pipeline.py
src/render/social_formats.py
src/render/subtitles.py
src/intelligence/viral_score.py
src/intelligence/emotional_score.py
src/analysis/subjects.py        ← YOLO; clipping-only
# dead stubs
src/render/effects.py
src/render/logo_animation.py
src/render/transitions.py
src/main.py                     ← orphaned entry stub (real entry is cli.py)
```

### Config & project files
```
✂️ COPY+STRIP : config.yaml (drop subjects/openclip/reframe_decision/zoom_crop_decision/thumbnails/api.twelvelabs sections)
✂️ COPY+STRIP : content_modes/standard_longform.yaml (delete inert keys per §4; or promote to the project's single config)
✅ COPY       : .pre-commit-config.yaml , .python-version
🆕 REWRITE    : pyproject.toml (new deps §3 + new name/package + entry) , README.md
❌ SKIP       : content_modes/cinematic_longform.yaml , content_modes/shortform.yaml , content_modes/*.bak
❌ SKIP       : .env (create a fresh .env.example — only needed if Descript) , the engine's CLAUDE.md / SPEC.md / KNOWLEDGE.md
```

### Tests (carry the standard subset)
```
✅ COPY       : tests/__init__.py , tests/test_ingestion.py , tests/test_parallel.py
✂️ COPY+STRIP : tests/conftest.py (drop excluded fixtures) , tests/test_analysis.py (drop subjects) ,
                tests/test_scoring.py (keep standard; drop viral/cinematic/emotional) ,
                tests/test_render.py (keep build_longform_command/run_longform_render/select_best_window/thumbnails; drop shortform reframe/caption + grade) ,
                tests/test_cli.py (keep process/reprocess/workspace; drop grade)
🔁 OPTIONAL   : tests/test_descript.py (only if Descript)
❌ SKIP       : tests/test_color_pipeline.py , tests/test_grade_report.py
fixtures      : copy only those referenced by the kept tests (e.g. sample_60s_1080p.mp4, sample_60s_16k_mono.wav,
                real_speech_sample.mp4 if transcription).  ❌ SKIP tests/fixtures/identity.cube (LUT fixture, grade-only).
```

### 3 gotchas that will bite if missed
1. **`render/__init__.py`** — do NOT copy the existing one (it's the shortform `run_render` orchestrator). Create a new empty/minimal `render/__init__.py` so `render` stays an importable package.
2. **Package name / imports** — the code is imported as the `src` package (relative imports internally; tests use `from src...`, and `pyproject` maps `packages = ["src"]`, entry `cme = "src.cli:app"`). Either keep the top package importable as `src`, or rename to e.g. `standard_longform` and update: every `from src.` in tests, `[tool.hatch.build.targets.wheel] packages`, and `[project.scripts]`.
3. **New `pyproject.toml`** — don't reuse the old one; it lists dead deps (scipy/Pillow/open-clip-torch/torchvision/twelvelabs). Use the §3 map (core drops the entire torch/ML stack unless you keep `--transcribe`).

**Count:** ~24 files copy-as-is, 5 copy-and-strip, +2 (transcription) / +3 (Descript) optional, the rest skipped.
