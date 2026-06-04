# Cinematic Media Engine

## Project Brief for Claude Code

**Owner:** kkthedon
**Project:** A multi-style media intelligence system that ingests raw or edited video and produces long-form videos, short-form clips, and AI-generated thumbnails. Pipeline behavior, scoring, pacing, rendering, and export strategy adapt based on the selected content mode.

**Three content pillars served by one engine:**
1. Stream highlights and gameplay (Standard Longform)
2. Music process and artistic content (Cinematic Longform)
3. Daily vlogs and social clips (Shortform)

---

## 1. Folder Structure

```
cinematic-media-engine/
├── README.md
├── SPEC.md
├── CLAUDE.md
├── pyproject.toml
├── config.yaml
├── .env.example
├── .gitignore
│
├── src/
│   ├── main.py
│   ├── cli.py
│   ├── config.py
│   │
│   ├── ingestion/
│   │   ├── validator.py            # Verify input file, codec, duration
│   │   ├── metadata.py             # ffprobe-based metadata extraction
│   │   ├── audio_extract.py        # ffmpeg audio extraction for analysis
│   │   └── workspace.py            # Working directory management
│   │
│   ├── analysis/
│   │   ├── opencv.py               # Motion detection, scene change, framing
│   │   ├── openclip.py             # Aesthetic and visual composition scoring
│   │   ├── whisper.py              # WhisperX: transcription + wav2vec2 word-level alignment
│   │   ├── librosa_analysis.py     # Audio energy, speech intensity, peaks
│   │   ├── twelvelabs.py           # Semantic video understanding (API)
│   │   ├── hooks.py                # Viral hook phrase + quotable detection
│   │   ├── aesthetics.py           # Lighting, color, composition scoring
│   │   └── scoring.py              # Per-signal normalization and weighting
│   │
│   ├── intelligence/
│   │   ├── cinematic_score.py      # Composite scoring for cinematic mode
│   │   ├── viral_score.py          # Composite scoring for shortform mode
│   │   ├── emotional_score.py      # Emotional moment detection
│   │   ├── pacing_engine.py        # Mode-aware pacing rules
│   │   ├── clustering.py           # Group nearby moments into segments
│   │   └── ranking.py              # Final ranked moment list
│   │
│   ├── render/
│   │   ├── ffmpeg_pipeline.py      # Core ffmpeg orchestration
│   │   ├── effects.py              # Zoom, speed ramps, motion effects
│   │   ├── subtitles.py            # Caption generation and burn-in
│   │   ├── transitions.py          # Cut, fade, cinematic transitions
│   │   ├── logo_animation.py       # Animated brand intros/outros
│   │   ├── social_formats.py       # 9:16, 1:1, 16:9 format handlers
│   │   ├── thumbnails.py           # AI thumbnail candidate generation
│   │   └── color_pipeline.py       # LUT application, grading
│   │
│   ├── branding/
│   │   ├── overlays/               # Watermark and overlay assets
│   │   ├── logos/                  # Logo variants
│   │   ├── fonts/                  # Caption and lower-third fonts
│   │   ├── luts/                   # Color grade LUT files
│   │   ├── presets/                # Mode-specific render presets
│   │   └── lower_thirds/           # Lower-third graphic templates
│   │
│   ├── output/
│   │   ├── shorts/                 # Vertical short-form output
│   │   ├── reels/                  # Reels-formatted output
│   │   ├── trailers/               # Short trailer/teaser output
│   │   ├── cinematic/              # Cinematic longform output
│   │   ├── captions/               # Caption files (SRT, VTT)
│   │   └── thumbnails/             # Generated thumbnail candidates
│   │
│   └── utils/
│       ├── ffmpeg.py               # Shared ffmpeg subprocess wrapper
│       ├── timestamps.py           # Time conversion helpers
│       ├── logging.py              # Structured logging
│       ├── progress.py             # Rich progress bars
│       └── fileio.py               # File I/O helpers
│
├── content_modes/
│   ├── standard_longform.yaml      # Standard longform mode config
│   ├── cinematic_longform.yaml     # Cinematic longform mode config
│   └── shortform.yaml              # Shortform clip mode config
│
├── tests/
│   ├── fixtures/                   # Small test videos
│   ├── test_ingestion.py
│   ├── test_analysis.py
│   ├── test_render.py
│   └── test_scoring.py
│
└── workspace/                      # Default working directory (gitignored)
    └── [run-id]/
        ├── audio.wav
        ├── transcript.json
        ├── analysis/
        │   ├── librosa.json
        │   ├── opencv.json
        │   ├── openclip.json
        │   ├── twelvelabs.json
        │   └── aesthetics.json
        ├── moments.json            # Final ranked output
        └── output/                 # Final rendered files
```

---

## 2. Content Modes

The engine supports three distinct content styles from the same source footage. The pipeline behavior, scoring logic, pacing, rendering, and export strategy change depending on the selected content mode.

### 2.1 Standard Longform Mode

**Purpose:** Traditional long-form creator content.

**Examples:**
- Gameplay videos
- Stream uploads
- Podcasts
- Commentary
- Casual vlogs

**Goals:**
- Preserve chronology
- Preserve conversational flow
- Maintain audience retention
- Minimize over-editing
- Produce clean watchable content

**Editing Characteristics:**
- Lighter transitions
- Minimal effects
- Continuity-focused pacing
- Cleaner cuts
- Lower clip density
- Subtle branding

**Priority Signals:**
- Transcript quality
- Pacing consistency
- Narrative continuity
- Conversational moments

**Outputs:**
- YouTube longform videos
- Archive videos
- VOD-style uploads

---

### 2.2 Cinematic Longform Mode

**Purpose:** Emotionally driven cinematic creator content.

**Examples:**
- Music process videos
- Artistic vlogs
- Documentaries
- Storytelling videos
- Cinematic creator edits

**Goals:**
- Emotional immersion
- Cinematic pacing
- Visual consistency
- Aesthetic storytelling
- Mood preservation

**Editing Characteristics:**
- Cinematic transitions
- Atmospheric pacing
- Color grading (LUT-based)
- Motion graphics
- Music synchronization
- Visual storytelling

**Priority Signals:**
- OpenCLIP aesthetic scoring
- Emotional moments
- Lighting quality
- Pacing curves
- Visual composition
- Soundtrack synchronization

**Outputs:**
- Cinematic YouTube videos
- Artist documentaries
- Branded storytelling content

---

### 2.3 Shortform Clip Mode

**Purpose:** High-retention short-form social media content.

**Examples:**
- TikTok clips
- Instagram Reels
- YouTube Shorts
- Viral moments
- Hook-based edits

**Goals:**
- Immediate attention capture
- Retention optimization
- Rapid pacing
- Viral potential
- Platform optimization

**Editing Characteristics:**
- Aggressive pacing
- Dense editing
- Captions burned in
- Zoom effects
- Rapid transitions
- Hook prioritization
- High-energy sequencing

**Priority Signals:**
- Speech intensity
- Quotable dialogue
- Audio peaks
- Fast motion
- Emotional reactions
- Viral hook phrases

**Outputs:**
- TikTok-formatted clips
- Reels-formatted clips
- YouTube Shorts
- Social media clips

---

## 3. Mode-Aware Intelligence

The intelligence engine adapts behavior depending on content mode. This includes:

- Scoring weights (which signals matter most)
- Pacing rules (cut density, segment length)
- Transition behavior (hard cuts vs cinematic transitions)
- Subtitle style (burn-in vs separate file)
- Branding intensity (subtle vs prominent)
- Render presets (resolution, codec, color)
- Export formatting (aspect ratio, platform-specific)
- Clip density (sparse for longform, dense for shortform)

Each content mode uses its own:
- Scoring profile
- Rendering profile
- Branding profile
- Pacing profile

These profiles live in `content_modes/*.yaml` and are loaded at runtime based on the `--mode` flag.

---

## 4. CLI Interface

```bash
# Standard longform (default for stream content)
cme process input.mp4 --mode standard_longform

# Cinematic longform (default for music process)
cme process input.mp4 --mode cinematic_longform

# Shortform clips (clips from any source)
cme process input.mp4 --mode shortform

# Run multiple modes from same source in one pass
cme process input.mp4 --mode standard_longform --mode shortform

# Override config defaults
cme process input.mp4 --mode shortform --top-n 20

# Skip TwelveLabs (local-only, faster, free)
cme process input.mp4 --mode shortform --no-twelvelabs

# Workspace management
cme workspace list
cme workspace clean --older-than 7d

# Reprocess from cached workspace
cme reprocess workspace/run_20260524_184200/ --mode cinematic_longform
```

---

## 5. Pipeline Flow

```
[INPUT: Raw or edited video file]
            │
            ▼
[Ingestion Layer]
  validator → metadata → audio_extract → workspace
            │
            ▼
[Analysis Layer — parallel execution]
  ├─ whisper.py          ─► Transcript with timestamps
  ├─ librosa_analysis.py ─► Audio energy + speech intensity
  ├─ opencv.py           ─► Motion + scene change detection
  ├─ openclip.py         ─► Visual aesthetic scoring
  ├─ aesthetics.py       ─► Lighting + composition
  ├─ hooks.py            ─► Viral hook detection (transcript-based)
  └─ twelvelabs.py       ─► Semantic understanding (API)
            │
            ▼
[Intelligence Layer — mode-aware]
  scoring.py → composite scoring per signal
  → cinematic_score.py | viral_score.py (mode-dependent)
  → emotional_score.py
  → pacing_engine.py (applies mode-specific pacing rules)
  → clustering.py
  → ranking.py
            │
            ▼
[Render Layer — mode-aware]
  ffmpeg_pipeline.py orchestrates:
  ├─ effects.py (zooms, speed ramps for shortform)
  ├─ transitions.py (mode-specific)
  ├─ subtitles.py (burn-in for shortform, separate for longform)
  ├─ color_pipeline.py (LUTs for cinematic)
  ├─ logo_animation.py (mode-dependent intensity)
  ├─ social_formats.py (9:16, 1:1, 16:9)
  └─ thumbnails.py (longform modes)
            │
            ▼
[OUTPUT]
  output/shorts/      → 9:16 social clips
  output/reels/       → Reels-formatted
  output/trailers/    → Teaser/trailer cuts
  output/cinematic/   → Graded longform
  output/captions/    → SRT/VTT files
  output/thumbnails/  → Thumbnail candidates
```

---

## 6. Technical Stack

| Component | Tool | Notes |
|-----------|------|-------|
| Language | Python 3.11+ | Single language for pipeline |
| Video/audio | ffmpeg | Subprocess calls |
| Semantic video AI | TwelveLabs API | Cloud, free tier OK to start |
| Transcription | WhisperX (faster-whisper + wav2vec2) | faster-whisper CTranslate2 backbone + wav2vec2 forced alignment → <100ms word timestamps. NOT openai-whisper. Mode-conditional. |
| Audio analysis | librosa | CPU, fast |
| Motion/scene | OpenCV + PySceneDetect | CPU |
| Visual aesthetic | OpenCLIP | GPU preferred |
| CLI | Typer | Type-safe CLI |
| Config | YAML + Pydantic | Validated schemas |
| Progress | Rich | Terminal output |

### Hardware Notes

- M1 MacBook handles long-form rendering and OpenCLIP inference well
- faster-whisper `small` model fits comfortably
- ffmpeg leverages M1 hardware encoding for fast exports

---

## 7. Configuration Structure

### Root config.yaml
```yaml
defaults:
  mode: standard_longform
  workspace_root: ./workspace
  retention_days: 7

api:
  twelvelabs:
    api_key_env: TWELVELABS_API_KEY
    index_id_env: TWELVELABS_INDEX_ID

transcription:
  backend: whisperx          # faster-whisper (CTranslate2) + wav2vec2 alignment. NOT openai-whisper.
  model_size: small          # precision comes from alignment, not size
  device: cpu                # Whisper step CPU-only on M1 (CTranslate2 has no Metal backend)
  compute_type: int8
  language: en
  word_timestamps: true
  vad_filter: true
  condition_on_previous_text: false
  alignment: { enabled: true, device: auto }   # wav2vec2, MPS on M1
  diarization: { enabled: false }              # single creator
  audio_preprocess: { loudnorm: true, sample_rate: 16000, mono: true }

output:
  base_dir: ./src/output
  formats:
    shorts: { width: 1080, height: 1920, fps: 30 }
    reels:  { width: 1080, height: 1920, fps: 30 }
    cinematic: { width: 3840, height: 2160, fps: 24 }
    standard:  { width: 1920, height: 1080, fps: 30 }
```

### content_modes/standard_longform.yaml
```yaml
mode_name: standard_longform

scoring_weights:
  transcript_quality: 0.30
  pacing_consistency: 0.25
  narrative_continuity: 0.20
  conversational: 0.15
  audio_energy: 0.10

pacing:
  min_segment_sec: 30
  max_segment_sec: 300
  preserve_chronology: true
  dead_air_threshold_sec: 8

rendering:
  transitions: hard_cut
  effects_intensity: minimal
  subtitles: separate_file
  branding_intensity: subtle
  color_grade: none

output_targets:
  - youtube_longform

clip_density: low
```

### content_modes/cinematic_longform.yaml
```yaml
mode_name: cinematic_longform

scoring_weights:
  openclip_aesthetic: 0.30
  emotional_moments: 0.20
  lighting_quality: 0.15
  visual_composition: 0.15
  pacing_curve: 0.10
  soundtrack_sync: 0.10

pacing:
  min_segment_sec: 8
  max_segment_sec: 60
  preserve_chronology: false
  atmospheric_pacing: true

rendering:
  transitions: cinematic
  effects_intensity: moderate
  subtitles: optional_styled
  branding_intensity: refined
  color_grade: lut_based
  default_lut: branding/luts/kkthedon_cinematic.cube

output_targets:
  - cinematic_youtube
  - artist_documentary

clip_density: medium
```

### content_modes/shortform.yaml
```yaml
mode_name: shortform

scoring_weights:
  speech_intensity: 0.25
  audio_peaks: 0.20
  viral_hooks: 0.20
  emotional_reactions: 0.15
  quotable_dialogue: 0.10
  fast_motion: 0.10

pacing:
  min_segment_sec: 5
  max_segment_sec: 60
  aggressive_cuts: true
  hook_first_seconds: 3

rendering:
  transitions: rapid
  effects_intensity: high
  subtitles: burned_in
  branding_intensity: prominent
  aspect_ratio: 9:16
  zoom_effects: true
  speed_ramps: true

output_targets:
  - tiktok
  - reels
  - shorts

clip_density: high
target_clips_per_session: 20
```

---

## 8. Implementation Phases

### Phase 1 — Foundation
- Project scaffold (pyproject.toml, folder structure, .gitignore, .env.example)
- config.py with Pydantic schemas
- cli.py with Typer entry point, all commands stubbed
- utils/ modules (ffmpeg, timestamps, logging, progress, fileio)
- Ingestion layer fully working
- Tests for ingestion

### Phase 2 — Analysis Layer
- whisper.py (faster-whisper)
- librosa_analysis.py
- opencv.py (motion + scene detection)
- hooks.py (transcript-based)
- aesthetics.py (lighting + composition basics)
- All write to workspace/[run-id]/analysis/
- Tests with fixture video

### Phase 3 — Intelligence + Scoring
- scoring.py (per-signal normalization)
- cinematic_score.py
- viral_score.py
- emotional_score.py
- pacing_engine.py (mode-aware)
- clustering.py
- ranking.py
- moments.json output
- End-to-end test with `--no-twelvelabs` flag

### Phase 4 — Render Layer
- ffmpeg_pipeline.py core
- transitions.py
- effects.py
- subtitles.py
- social_formats.py
- color_pipeline.py
- thumbnails.py
- logo_animation.py
- Mode-specific render paths

### Phase 5 — OpenCLIP + TwelveLabs
- openclip.py for aesthetic scoring
- twelvelabs.py API integration
- Integration into cinematic_score and viral_score
- Async upload/indexing while local analysis runs

### Phase 6 — Polish
- Rich progress bars on long-running steps
- Resume capability (cached workspace detection)
- `cme reprocess` command
- `cme workspace` commands
- README with setup, usage, troubleshooting per mode

---

## 9. Error Handling Requirements

- **Invalid input file:** Clear error, exit 1, suggest ffmpeg conversion
- **GPU OOM (OpenCLIP, Whisper):** Auto-fallback to CPU with warning
- **TwelveLabs API failure:** Continue with local-only scoring, log warning
- **Missing LUT file (cinematic mode):** Warn, skip grading, continue
- **No face detected (where needed):** Skip face-dependent signal, redistribute weights
- **Interrupted run:** Workspace preserved, `cme reprocess` resumes

---

## 10. Performance Targets

For a 60-min 1080p source on M1 MacBook:

| Step | Target time |
|------|-------------|
| Ingestion | < 30 sec |
| faster-whisper small | 8-12 min |
| Audio analysis (librosa) | < 1 min |
| OpenCV motion/scene | 2-4 min |
| OpenCLIP aesthetic (sampled) | 3-6 min |
| Aesthetics + hooks | 1-2 min |
| TwelveLabs upload + indexing | 8-15 min (parallel with local) |
| Scoring + clustering | < 30 sec |
| Render (mode-dependent) | 2-10 min |
| **Total end-to-end** | **25-45 min, mostly unattended** |

Parallel execution where possible. TwelveLabs upload kicks off in parallel with local analysis.

---

## 11. Key Design Decisions

1. **One engine, three modes** — Same analysis pipeline, mode-specific scoring/rendering. Adding a fourth mode = new yaml + new scoring profile, no architectural changes.

2. **YAML mode configs not CLI flags** — Tuning weights is iterative. Editing a config file is faster than memorizing 30 flags.

3. **Workspace-per-run** — Resume capability + intermediate artifact inspection + clean separation between runs.

4. **TwelveLabs optional** — Local-only mode runs free. TwelveLabs adds semantic depth when worth the API cost.

5. **WhisperX, not plain faster-whisper or openai-whisper** — WhisperX runs faster-whisper (CTranslate2) for the transcript, then a wav2vec2 forced-alignment pass for word timestamps accurate to <100ms (vs ~1s drift on raw Whisper). Word-timestamp precision is the critical axis for clip cutting and burned captions. Still CTranslate2 under the hood (never openai-whisper). Transcription is mode-conditional (shortform always; cinematic + standard opt-in via `--transcribe`). MLX-Whisper was evaluated and rejected as primary — faster on M1 but worse word timestamps — and is kept only as a documented future fast-path for non-timestamp-critical bulk long-form.

6. **Mode-aware rendering** — Shortform burns subtitles, cinematic applies LUTs, standard keeps cuts minimal. Same render layer, different paths.

7. **Composite scoring rewards convergence** — Real best moments score high on multiple signals. Composite scoring is robust to false positives in any single signal.

8. **Clustering before output** — Prevents top moments all clustering in one 2-minute window. Min-gap forces diversity.

---

## 12. Out of Scope (Explicit Non-Goals)

- Live/real-time clipping
- Multi-camera angle switching
- Auto-upload to platforms (separate distribution system handles this)
- Music generation
- Voice synthesis
- Automatic title generation (can add via Claude API call later)
- Stock B-roll insertion
- Music library management

---

## 13. Setup Instructions (for README)

```bash
# 1. Clone and enter project
git clone <repo>
cd cinematic-media-engine

# 2. Install Python deps
uv sync

# 3. Install ffmpeg
brew install ffmpeg  # M1 Mac

# 4. Configure API keys
cp .env.example .env
# Edit .env, add TWELVELABS_API_KEY and TWELVELABS_INDEX_ID

# 5. Place brand assets
# Add LUTs to src/branding/luts/
# Add logos to src/branding/logos/
# Add fonts to src/branding/fonts/

# 6. Review/edit content_modes/*.yaml for your style

# 7. Run
cme process /path/to/video.mp4 --mode shortform
```

---

## 14. What Claude Code Should Build First

In strict order:

1. **Phase 1 in full** — scaffold, CLI, config, ingestion. Verify by running `cme process test.mp4 --mode shortform` and seeing workspace created with extracted audio. NO analysis yet.

2. **Phase 2 modules one at a time** — start with librosa (simplest), then opencv, then whisper, then hooks, then aesthetics. Test each in isolation before moving on. All write JSON to workspace/[run-id]/analysis/.

3. **Phase 3 — scoring** — build with synthetic data first to verify math, then plug real signal outputs in. Implement all three mode-scoring files but test one mode at a time.

4. **Phase 4 — render layer** — start with shortform mode rendering (simplest, single aspect ratio, burned subs). Then standard_longform. Cinematic last because color grading is the most complex.

5. **Phase 5 — OpenCLIP + TwelveLabs LAST.** Entire system must work without them. They add quality on top of a working foundation.

6. **Phase 6 — Polish** — only when everything works.

**Do not skip ahead. Do not try to write all phases at once. Ship Phase 1, verify, then move on.**

After every phase: commit, verify tests pass, start a fresh Claude Code session for the next phase to keep context clean.
