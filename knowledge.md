# KNOWLEDGE.md — Cinematic Media Engine

This file is the project's evolving memory. Claude Code reads it at the start of every session. Update it whenever a non-trivial decision is made, a library quirk is discovered, or a tuning value is dialed in.

**Update rule:** If a future Claude Code session would benefit from knowing this, write it down.

---

## Project Identity

- **Name:** Cinematic Media Engine
- **Owner:** kkthedon
- **Purpose:** Multi-style media intelligence — three content modes from one engine
- **Hardware:** Mac M1 MacBook (primary), Sony ZV-E10 (capture)
- **Not for sale.** Internal tool only.

---

## Decisions Made (and Why)

### Architecture

- **One engine, three modes** — Same pipeline, mode-specific configs. Adding modes = new YAML, no code changes.
- **Workspace-per-run** — Resume capability, debuggability, clean separation.
- **TwelveLabs is optional** — Local-only mode runs free and offline. TwelveLabs adds quality, not requirement.
- **WhisperX over plain faster-whisper** — WhisperX = faster-whisper (CTranslate2) for the transcript + wav2vec2 forced alignment for word timestamps. Word boundaries land <100ms vs ~1s drift on raw Whisper. Word-timestamp precision is the make-or-break axis for short-form clip cutting and burned captions. Still CTranslate2 under the hood — NEVER openai-whisper. (Evidence: the prior `content_business` project bumped Whisper small→medium and built a karaoke-span render hack purely to mask Whisper's word-timing jitter — alignment removes the need for both.)
- **MLX-Whisper rejected as primary** — faster on M1 (runs on the GPU via Apple's MLX; faster-whisper is CPU-only on Mac because CTranslate2 has no Metal backend) BUT its `word_timestamps` quality is worse than WhisperX. Timing precision is the axis we can't trade. Kept as a possible v2 fast-path for bulk long-form where exact word timing doesn't matter (see Open Questions).
- **CPU-only Whisper step is acceptable on M1** — CTranslate2 has no Metal backend, so the faster-whisper transcription step runs on CPU int8. The wav2vec2 alignment step uses MPS (torch backend). Net throughput on M1 is fine for our volumes, and the precision win from alignment outweighs the lost GPU on the transcription step.
- **Transcription is mode-conditional** — shortform ALWAYS transcribes (burned captions + transcript-based hook scoring). cinematic_longform and standard_longform are OFF by default, opt-in via `--transcribe`.
- **Alignment device = MPS-by-default with automatic CPU fallback (Session 3 fix, kkthedon-approved).** wav2vec2 alignment prefers MPS (2–3× faster than CPU on M1), but MPS conv1d hits a hard limit on long audio (see Library Quirks) — so `align_transcript` (whisper.py) catches `NotImplementedError`/`RuntimeError` on the GPU path, logs a warning with the original error, and retries the whole alignment on CPU (SPEC §9 "auto-fallback to CPU with warning"). Rationale: keep the MPS speed win for the common (short-clip) case, guarantee no crash on long clips, and surface a warning so we can track if MPS failures become a pattern. Override with `CME_ALIGNMENT_DEVICE` (see Environment Setup Quirks). A CPU failure propagates (real error, not masked).
- **standard_longform SRT is gated behind `--transcribe`** — standard mode declares `subtitles: separate_file` (SRT), but with transcription off-by-default the SRT is only produced when `--transcribe` is passed; otherwise the subtitle config is ignored. YouTube's auto-captions cover the SEO use case for default uploads. Revisit if a chapter/SEO need emerges.
- **Mode-aware behavior in config, not code** — Editing weights and thresholds in YAML is faster than touching Python.

### Build philosophy

- **Ship working ugly before polished broken.** Hardcoded paths and rough error handling are fine in v1.
- **No sellable-product polish.** One user (kkthedon). No customer support, no docs beyond README, no clean APIs.
- **Boring code beats clever code.** Predictable patterns ship faster.
- **Dependency pins: ranges in pyproject, exact in uv.lock.** Use compatible-release ranges (`>=X,<next-major`) in pyproject.toml so uv can resolve a working combination; commit `uv.lock` for exact reproducibility. (The Session-0 exact `==` pins were never resolved and were mutually unsatisfiable — see Library Quirks.) Interpreter pinned to Python 3.11 via `.python-version`; `requires-python` capped to `<3.13` to avoid darwin wheel gaps on bleeding-edge Python.
- **Test fixtures are synthetic + generated, not committed.** `tests/conftest.py` renders a 60s/1080p test-pattern video (ffmpeg `testsrc` + 440 Hz `sine`) on demand; `tests/fixtures/*.mp4` and `*.wav` are gitignored. Keeps the repo lean and makes tests reproducible on any machine (and dodges the 5 MB large-file pre-commit hook).

### Analysis layer (Phase 2)

- **Analysis runs SEQUENTIALLY in v1 (parallelism deferred to Phase 6).** `run_analysis` executes the signal modules in-process, one after another. Rationale: `opencv-python` (cv2) and `av` (pulled in via the torch/whisperx stack) each ship their own `libavdevice` dylib, and macOS warns the clash "may cause spurious casting failures and mysterious crashes." Single-process decode is fine, but `concurrent.futures.ProcessPoolExecutor` under the `spawn` start method re-imports cv2 in every worker — risky. This is a deliberate, approved deviation from SPEC §5 / CLAUDE.md rule 5; revisit as a Phase-6 polish item and test the dylib interaction explicitly when adding it. (Approved by kkthedon, Session 2.)
- **Each signal module = pure logic + thin I/O.** Detection logic (peaks, frame-motion, hook matching, frame-aesthetics) is a pure function unit-tested against synthetic inputs; the `analyze_*` entry points are integration-tested (marked `@pytest.mark.integration`) against the generated fixture. This keeps the model-free logic fast and deterministic.
- **librosa `speech_intensity` = band-limited RMS, not raw energy.** `energy` is full-band RMS (standard_longform `audio_energy`); `speech_intensity` is RMS restricted to the 300–3400 Hz telephony speech band, normalized to [0,1] (shortform `speech_intensity`). Distinct signals from the same load. Beat/drop detection (cinematic soundtrack-sync) is NOT implemented — no config params exist for it; deferred.
- **Peak detection is global z-score, not rolling.** Peaks = frames > `peak_threshold_std` (1.5) std above the global mean, with non-max suppression enforcing `min_peak_gap_sec` (3.0). Flat signal (std≈0) → no peaks (guards div-by-zero). "Rolling mean" in the config comment was simplified to global for v1 (short clips); revisit for long-form.
- **aesthetics metrics are cv2/numpy placeholders, to be replaced by OpenCLIP in Phase 5 (confirmed).** lighting_quality = mid-exposure proxy `1-2|b-0.5|`; composition_score = contrast proxy `std/128`; sharpness = normalized variance-of-Laplacian; color_harmony = Hasler-Süsstrunk colorfulness. All clamped [0,1]. These are explicitly interim: Phase 5 (`openclip.py` + cinematic_score) WILL supersede them with OpenCLIP-based aesthetic/composition scoring. Unknown metric names in `aesthetics.metrics` are ignored, so the openclip-backed metrics can be added without breaking this module. (Approved by kkthedon, Session 2.)
- **Hook matching is token-based, not substring.** "no way" must not match "norway"; phrases match contiguous word-token runs. `within_window` = phrase lands inside the segment's first `hook_window_sec`. Quotable detection = min-word-count heuristic (placeholder, tune on real footage).
- **transcript.json lives at the workspace root, not analysis/.** Per SPEC §1. Other signals write to `analysis/<name>.json`. The source video path is recoverable from `metadata.json` (`MediaMetadata.path`) for Phase-6 reprocess.
- **WhisperX real-model test is opt-in.** `tests/test_analysis.py::test_transcribe_audio_real_model` is `@pytest.mark.slow` + skipped unless `CME_RUN_WHISPER_MODEL=1` (the model stack is ~800MB). Keeps the default suite and pre-commit fast. The pure transcript-assembly (`build_transcript`) is always tested. The full path was verified once via `cme process ... --mode shortform` (see below) — both transcribe and align ran (exit 0).
- **The steady-tone fixture makes WhisperX hallucinate one token ("you").** Even with VAD on, faster-whisper emits a single segment/word on the 440 Hz tone, so the alignment branch DOES execute (`aligned=true` in the output) — transcribe+align code paths are verified end-to-end. But alignment *timing quality* on real speech still needs a speech fixture to validate.
- **wav2vec2 alignment model logs a benign torch-version warning.** "Model was trained with torch 1.10.0+cu102, yours is 2.5.1. Bad things might happen unless you revert torch to 1.x." This is the torchaudio wav2vec2 alignment bundle; it runs fine on torch 2.5.1 — informational, not an error.

### Intelligence + scoring (Phase 3)

- **Shortform clips are locked to exactly 60 seconds** for cross-platform monetization compatibility: 60s clears TikTok's monetization minimum, hits the YouTube Shorts algorithmic sweet spot, and stays within Instagram Reels' native length. Every shortform output is monetizable on every platform without exception. (`content_modes/shortform.yaml` pacing: `min_segment_sec = max_segment_sec = preferred_duration_sec = 60`.)
- **Trade-off accepted:** occasional sub-optimal natural moment length is traded for guaranteed platform compliance. Phase 3 ranking therefore selects the strongest 60s *window* (not the peak instant); Phase 4 handles pre-roll padding when the natural moment is shorter and trimming/capping at 60s ending on a natural close when it's longer.
- **Approved by kkthedon, Session 3 pre-implementation.**

**Phase-3 design decisions (Session 3, kkthedon-approved). Each notes the rejected alternatives + where to change it if we revisit.**

- **Window selection = sliding 60s window, mean composite.** Build a per-second composite-score timeline, slide a fixed-length window (60s for shortform) at 1s step, score each window as the *mean* composite inside it, then greedy-pick non-overlapping windows by score honoring `diversity.min_gap_sec` / `max_clips_per_minute`. Sub-window-length sources clamp the window to source length (the 30s fixture → one window over [0, dur]). *Rejected:* anchor-on-peaks-then-extend (biases to instantaneous peaks, misses the true best window); cluster-first-then-snap (more moving parts, awkward under a fixed length). *To change:* the windowing lives in `clustering.py` (candidate generation + greedy selection); swap the candidate-generation strategy there. Mean-vs-max window aggregation is a one-line change.
- **Emotional signal = audio-dynamics proxy** (`emotional_score.py`). `emotional_intensity(t)` = min-max-normalized rate-of-change of RMS energy (|ΔRMS| envelope; raw first-difference magnitude, no smoothing in v1), computed from `librosa.json` ONLY (no transcript), so cinematic (default no-transcribe) gets a real signal too. Feeds shortform `emotional_reactions` (0.15) and cinematic `emotional_moments` (0.20). Deliberately distinct from `audio_peaks` (discrete peak density) and `speech_intensity` (band level). This is an INTERIM proxy — same status as the cv2 aesthetics placeholders — to be superseded by face/OpenCLIP/TwelveLabs emotion in Phase 5. *Rejected:* mark-unavailable-and-redistribute (loses a real signal we can cheaply compute); transcript-sentiment heuristics (no transcript in cinematic-default; needs tuning). *To change:* replace the formula in `emotional_score.py::emotional_timeline`.
- **Normalization = per-run min-max, flat→0** (`scoring.py`). Each signal scaled `(x-min)/(max-min)` over the run to [0,1]; already-[0,1] signals (speech_intensity, aesthetics) pass through the same min-max for consistency; a flat signal (max==min) maps to 0.0. Matches config `scoring.normalize_range: [0,1]`. *Rejected:* z-score→logistic squash (less interpretable, compresses range); hybrid passthrough (inconsistent treatment). *To change:* `scoring.py::normalize`.
- **Signal→weight-key mapping + redistribution.** shortform: all 6 keys map to available signals (speech_intensity←librosa.speech_intensity, audio_peaks←peak-proximity envelope, viral_hooks←hooks.hooks conf-gated, emotional_reactions←emotional_score, quotable_dialogue←hooks.quotables conf-gated, fast_motion←opencv.motion) → NO redistribution in `--no-twelvelabs`. cinematic: `openclip_aesthetic` (Phase 5) + `soundtrack_sync` (no beat detection, deferred) are UNAVAILABLE → dropped and remaining weights renormalized to 1.0 (SPEC §9/§11.7); `pacing_curve` is a motion+energy rhythm placeholder. standard: `transcript_quality` (only if `--transcribe`) + `narrative_continuity` (semantic, needs TwelveLabs) UNAVAILABLE → redistribute. Effective weights + `unavailable_signals` are recorded in `moments.json`.
- **No `standard_score.py`.** SPEC §1 defines only `cinematic_score.py` + `viral_score.py`. standard_longform is scored by the GENERIC weighted_sum path in `scoring.py` (no bespoke module) — faithful to SPEC, not an omission. Shortform is the only fully-exercised mode in Phase 3; cinematic is implemented but redistribution-heavy; standard rides the generic path.
- **Word-confidence filter <0.3 applied at the WORD level** to transcript-derived signals. hooks.json/quotables carry no per-word score, so Phase 3 cross-references `transcript.json`: a hook hit counts only if its matched word(s) score ≥0.3; a quotable's strength counts only words ≥0.3 (dropped if it falls under the min-word threshold). Known fixture implication: `real_speech_sample.mp4` quotable[0] leads with `again,` @ score 0.066 (<0.3) — that word is excluded by design. **Exact threshold = 0.3** (rule: keep words with `score >= 0.3`, drop below). Defined as the module constant `MIN_WORD_CONFIDENCE = 0.3` in `scoring.py` — tunable there without a config-schema change. Promote to `config.yaml` (`scoring.min_word_confidence`) only if per-run control is needed later (that's a schema change → stop-and-ask). Values to consider when tuning on real footage: 0.3 (lenient, default) → 0.4 (stricter).
- **moments.json schema** = `{run_id, mode, source, generated_at, source_duration_sec, window_sec, composite_method, score_threshold, diversity{min_gap_sec,max_clips_per_minute}, weights{configured,effective,unavailable_signals}, moments[{rank,start_sec,end_sec,duration_sec,composite_score,scene_snapped,signal_contributions{}}], moment_count}`. `signal_contributions` = weight·normalized_signal and sums to `composite_score` (transparency for tuning). Pydantic-modelled, written to `workspace.moments_path`.
- **Scene-snapping degrades gracefully** — snap window boundaries to nearest `opencv.scene_cuts_sec` within tolerance when cuts exist, else no-op (`scene_snapped=false`). The fixture has `scene_count=1`/no cuts, so the smoke test exercises the fallback, not snapping.
- **`hook_first_seconds` is a SOFT signal in Phase 3, not a hard gate** — captured via the `viral_hooks` weight, not a "must contain a hook or discard" rule (a hard rule would zero out the hookless fixture). Revisit if real footage shows hookless clips slipping through.
- **Threshold is NOT lowered to force a moment on the 30s fixture.** If the single clamped window scores below shortform `score_threshold: 0.55`, `moments.json` is valid but empty; the raw window score is reported in the smoke output. Per the anti-pattern "don't tune before seeing real output" — we look at the number first, then decide on real footage.

**Phase-3 patch (Session 4, 2026-05-27, kkthedon-approved) — diagnostic visibility + fallback:**

- **`moments.json` always reports `best_window` (per-signal breakdown), even on zero output.** Replaces the scalar `best_window_score` (clean break, no tech debt). `best_window` = `{start_sec, end_sec, composite_score, signal_contributions (weight·mean, sum to composite), signal_means (raw normalized windowed means)}`. The `signal_means` are the lens for tuning *weights* over time; contributions show weighted impact. Always populated so we can see WHY a run scored where it did.
- **Top-N fallback guarantees output (`top_n_fallback`, default 5).** If no window clears `score_threshold`, `ranking.select_moments` returns the top N candidates as `below_threshold_best_candidates` with `used_fallback: true`; `moments[]` stays strictly threshold-passers. Plumbed via `PacingPlan.top_n_fallback` (default `DEFAULT_TOP_N_FALLBACK=5`); shortform sets it explicitly. The threshold is NEVER lowered to force results.
- **Threshold held at 0.55 (Fix A skipped).** Diagnostic data (MrBeast best window 0.361) showed lowering to 0.40 would STILL yield zero (the ~0.36 ceiling), so a threshold change would mislead, not help. The threshold becomes meaningful again only after the Phase 3.5 window-aggregation rework (see Phase 3.5 section).
- **Mean aggregation identified as the structural score ceiling.** `composite = Σ weightₖ·mean_over_window(normalizedₖ)`. Windowed *means* of per-run-min-max-normalized spiky signals (peaks, motion, emotion) sit ~0.25–0.48, so composites compress to ~0.25–0.36 with almost no spread — independent of content energy. Root lever, deferred to Phase 3.5, out of scope for this patch.

### Intelligence + scoring (Phase 3.5, Session 4, 2026-05-27, kkthedon-approved) — window aggregation

- **Window aggregation: mean → 90th percentile.** `clustering.slide_windows` now collapses each signal's in-window grid slice via `scoring.aggregate_window(values, method, percentile)` instead of the mean. Percentile captures the window's near-peak so spikes survive instead of regressing to the mean. Verified on MrBeast.mp4: best window **0.361 → 0.744**, window-score spread **0.11 → 0.28** (0.46–0.74), and **10 moments now clear 0.55** where mean produced zero. Backward-compatible: `slide_windows`/`build_pacing_plan` default to `mean` (so existing unit tests stay green); production switches to percentile via config.
- **Single global percentile, NOT per-signal (YAGNI).** One knob (`window_aggregation`/`window_percentile`) in `config.yaml scoring:` (global default `percentile`/`90`), overridable per-mode (shortform sets it explicitly). A single high percentile already does the right thing across continuous curves, influence envelopes, and the coverage mask. Per-signal aggregation is a trivial future extension if tuning data ever justifies it — don't build it speculatively.
- **`signal_means` → `signal_values` rename.** Under percentile the per-signal value in `best_window`/`WindowCandidate` is no longer a mean, so the field name was corrected to match its contents (the aggregated value that feeds the composite; contributions = weight·value). Done while the schema was one day old.
- **`score_threshold` held at 0.55 (re-evaluated, not changed).** With spread restored, 0.55 yields 10 diverse moments on MrBeast (375/542 windows qualify → diversity is the real selector; capped at 10 by min_gap 30s / max 2/min). 0.55 is *below* MrBeast's median window (0.62) — permissive on this dense content, but holding it avoids over-fitting to one clip; a calmer video would dip lower and 0.55 becomes appropriately selective. Revisit once a calmer-content sample exists. The threshold is now MEANINGFUL again (was unreachable under mean).

### Render layer (Phase 4, Session 5, 2026-05-27, kkthedon-approved) — shortform first

- **Render backend abstraction (kkthedon kickoff requirement).** The engine renders through a `RenderBackend` Protocol (`render/backend.py`) taking a backend-agnostic `ClipRenderSpec`; `FFmpegBackend` (`ffmpeg_pipeline.py`) is the first concrete impl and the ONLY place ffmpeg is shelled out. A future Descript backend slots in without touching the orchestrator (`run_render` accepts an injected backend). `render/backend.py` is a small addition not in SPEC §1 — justified by the explicit interface requirement.
- **Shortform render path = cut → 9:16 reframe → burn captions (+ optional watermark) → encode.** `run_render` reads moments + transcript, builds one `ClipRenderSpec` per kept moment (or per `below_threshold_best_candidates` when none cleared threshold), and renders each. Wired into `cme process` GATED to `mode==shortform` (standard/cinematic render paths not built yet — they print a "not built" notice). build order per SPEC §14.4.
- **60s cap at render time.** Each clip is `[start_sec, min(end_sec, start_sec+60)]` — scene-snap can't push a clip over the platform max; sub-60 snapped spans render as-is. (Phase 3 produces ~60s windows; this enforces the hard cap.)
- **9:16 reframe (`shortform.yaml rendering.reframe_method`) — `blur_pad` is the DEFAULT (Session 6).** `reframe_filtergraph` (social_formats) returns a `-filter_complex` producing `[vout]`:
  - `blur_pad` (default): `[0:v]split` → blurred COVER background (`force_original_aspect_ratio=increase,crop,gblur=sigma=20`) + sharp FIT-INSIDE foreground (`force_original_aspect_ratio=decrease`) + centered `overlay`. Keeps ALL source content (full bodies, edge graphics/UI) — TikTok-style. `decrease` is source-aspect-agnostic (16:9 → 1080×608 centred, ~656px blurred bars; tall/square pillarbox gracefully).
  - `center_crop`: `scale=...:force_original_aspect_ratio=increase,crop=...` (full-bleed) — **LOSSY on non-centred content**: cover-scales ~1.78× and crops the sides, chopping subjects / edge graphics (confirmed on MrBeast: timer + FLOOR overlay + side pool were cut). Selectable but NOT the default.
  - Unknown method → warn + fall back to `blur_pad`. Face-aware crop still deferred (needs face detection, Phase 5+).
  - **`foreground_scale` (blur_pad only, default 1.2, Session 6):** enlarges the sharp band by fitting it inside a `foreground_scale`× box (`foreground_fit_box`), then the centred overlay clips L/R overflow. 1.0 = full fit (big bars); 1.2 → fg 1296×729, bars ~595px, ~108px (10%) cropped L/R. Clamped `[1.0, 3.16]` (`clamp_foreground_scale`); 3.16 = the 16:9→9:16 full-bleed point (at the cap it ≈ center_crop). Non-16:9 sources reach full-bleed at a different point but 3.16 is a safe ceiling; practical range is 1.0–1.5 (beyond ~1.5 you lose too much width — use center_crop or track). `fit_inside`/`foreground_fit_box` are tested; the filter stays source-agnostic (`force_original_aspect_ratio=decrease`).
- **Reframe method is CONTENT-DEPENDENT (Session 6).** `blur_pad` (default) for wide/busy/multi-subject (MrBeast-type) — full frame preserved, `foreground_scale` tunes bars. `center_crop` only when the subject is reliably centred (full-bleed, lossy on edges). **`track` (Phase 4.5, NOT built)** for solo full-body music/rapping content (kkthedon) — full-bleed, subject centred, NO blur bars; this is the next render build and needs subject tracking. Mapped in `shortform.yaml` comments.
- **`build_clip_command` uses `-filter_complex` (not `-vf`) — Session 6.** Required because blur_pad's split/overlay can't live in a linear `-vf` chain. The reframe graph produces `[vout]`; the caption burn appends `;[vout]subtitles=...[vsub]`; the command maps `-map [vout|vsub] -map 0:a?` (optional source audio). This is the filter_complex foundation a watermark overlay / future compositor will also use. `fit_inside(src,dst)` documents/verifies the fit math (1920×1080 → 1080×608, offset 656); the actual filter uses `force_original_aspect_ratio=decrease` (no source-dim probe needed).
- **Captions = grouped pop-on ASS lines (≤`max_words_per_line`), burned via libass `subtitles=` filter.** `subtitles.py` builds the ASS from transcript words (clip-relative timing). Per-word karaoke highlight deferred (Future Enhancements). Font: brand font if present (fontsdir wired), else **system-font fallback (Helvetica/Arial) with a warning** (SPEC §9) — brand `branding/fonts/caption_bold.ttf` is currently absent. Caption visibility isn't asserted by tests (integration test checks geometry/duration/audio); verified by watching real output.
- **Missing brand assets → warn + skip (SPEC §9).** Watermark (`kkthedon_watermark_prominent.png`) and brand font are absent; render warns and continues (no watermark overlay, system caption font). The whole `src/branding/` tree is gitignored owner IP.
- **Encoder: `h264_videotoolbox` (M1 HW) with automatic `libx264` fallback** on `FFmpegError` (SPEC §9 GPU-fallback pattern), mirroring the wav2vec2 alignment fallback.
- **Stubs (deferred this phase):** `effects.py` (zoom/speed), `transitions.py`, `color_pipeline.py` (shortform `color_grade: light` is a no-op), `thumbnails.py`, `logo_animation.py` — docstring-only modules marking intent per SPEC §1.
- **Testing:** pure builders (ffmpeg args, reframe filterchain, ASS-from-words, font resolution, `build_clip_specs`) unit-tested; `run_render` glue tested with an injected recording backend (no encode); ONE `@pytest.mark.integration` real ffmpeg encode of the synthetic 60s fixture asserts 1080×1920 + duration + audio. Real MrBeast render = manual smoke.
- **Composition zones — v1 = full-bleed + zoned captions (kkthedon-approved).** `shortform.yaml composition:` defines vertical zones (hook/identity/caption/dead, fraction pairs) + caption placement + face-framing targets. v1 ACTIVELY applies caption zoning only: captions are bottom-anchored in the caption_zone via `subtitles.caption_layout` (`caption_y_position 0.80` → ASS MarginV 384px, clears the bottom platform UI; `caption_safe_width 900` → 90px L/R margins; alignment 2; ≤4 words/line) — the real fix for dead-centre captions. Full-bleed `center_crop` is unchanged. **Face-aware fields (`crop_focal_y`, `face_fill_target`) are STORED for schema-completeness but INERT until Phase 5 face detection** — `run_render` logs a one-line INFO flagging them inactive. Deliberately did NOT fake them via banding: banding positions the video *rectangle*, not the face, so it loses full-bleed AND gets ripped out when Phase 5 face detection lands. When Phase 5 arrives: apply crop biasing + face-fill so the eyeline sits in the upper third (identity zone). For 16:9 sources, `center_crop` crops horizontally (full height kept) so `crop_focal_y` has no vertical room to bias regardless — another reason it needs face detection to be meaningful.

### Subject detection + auto-reframe (Phase 4.5, kkthedon-approved) — content-adaptive framing

- **Why:** `foreground_scale: 1.2` cropped needed content off wide MrBeast footage (timer, edges) → proves framing must be CONTENT-ADAPTIVE, not a fixed setting. The engine should detect what's in each clip and pick the reframe method + params automatically. "Dynamic" = the SYSTEM picks method + foreground_scale per clip from detection — NOT animated/motion blur (blur stays a normal gaussian; what adapts is whether/how much).
- **Two layers (multi-session): Layer 1 = detection (DONE, Session 7), Layer 2 = decision (DONE, Session 8).**
- **Layer 1 — detection (`analysis/subjects.py`, ANALYSIS stage).** YOLO (`ultralytics`, `yolov8n`, `person` class) over sampled source frames → `analysis/subjects.json` (per-frame `subject_count`, `group_bbox`, `spread_ratio` = group-width/frame-width, `is_solo`, raw boxes). Cached like other analysis signals (re-renders don't re-detect). **Chose YOLO over MediaPipe**: the decision needs multi-person body COUNTING + full-body boxes + spread (MediaPipe is solo-face/pose-oriented, weak at counting bodies in busy scenes). `ultralytics` added — torch stack UNCHANGED (still 2.5.1; ultralytics needs only torch>=1.8). Runs sequentially in-process (cv2+av libavdevice clash); YOLO lazy-imported (heavy); MPS with CPU fallback (SPEC §9, mirrors wav2vec2). Profile math (`union_bbox`/`spread_ratio`/`profile_frame`) is pure + unit-tested; real-model run is opt-in (`CME_RUN_YOLO_MODEL=1`, like the WhisperX model test). Config: `config.yaml subjects:` (enabled/model/device/sample_rate_hz/confidence).
- **Layer 2 — decision (DONE, Session 8; buckets re-tuned + diagnostic added Session 9, `render/auto_reframe.py`).** Reads `subjects.json` per moment window → content profile → picks method + foreground_scale. `reframe_method: auto` is the shortform default; `run_render` loads `subjects.json` and resolves each moment ONCE (`_reframe_for_moment` → `(ReframePlan, ReframeDecision)`), passing a precomputed-plan lookup to `build_clip_specs` (manual modes return a fixed plan; auto runs `plan_clip` per window). Final decisions:
  - **Aggregation = MEDIAN subject_count (flicker-robust) + MAX spread_ratio (conservative track guard)** over the window. Max-spread is deliberately strict: a single wide frame in the window blocks `track`. On busy MrBeast footage almost no 20s window sustains a tight solo, so auto correctly stays `blur_pad` there — track fires on genuinely sustained-solo segments (verified: 6s window at t=400 → track; t=280 crowd → blur_pad).
  - **Discrete BUCKETS** (tunable in `config.yaml reframe_decision:`): `median==1 & max_spread≤solo_track_max_width(0.5)`→`track` fs=1.0; `==2`→`blur_pad` fs=`bucket_2_fs`(**1.2**); `3–small_group_max(5)`→`blur_pad` fs=`bucket_3_5_fs`(**1.0**); everything else (0 people, **solo-but-too-wide**, or 6+ large group)→`blur_pad` fs=`bucket_6_plus_fs`(1.0). **Solo-but-wide** (median 1 but max_spread>0.5: subject spans the frame or transient extra bodies) deliberately falls through to blur_pad 1.0 — tracking a frame-spanning subject would crop content.
  - **Buckets re-tuned DOWN on real MrBeast output (Session 9, kkthedon):** `bucket_3_5_fs` 1.1→**1.0** and `bucket_2_fs` 1.3→**1.2**. Rationale: fs>1.0 enlarges the sharp band, which trims L/R edges; the owner's known-good manual render used fs=1.0 and preserved ALL content (full timer, full edges). "Tighter framing for narrower groups" is right in theory but loses content in practice on wide/busy footage. **Lesson: prefer keeping all content with bigger bars over cropping edges** — field names were also renamed `pair_fs/small_group_fs/large_group_fs` → `bucket_2_fs/bucket_3_5_fs/bucket_6_plus_fs` for tuning clarity.
  - **Reframe decision is written back into `moments.json` per moment** (`Moment.reframe = {method, foreground_scale, median_count, max_spread}`) — same idea as the per-signal scoring breakdown: inspect what the engine chose per clip without eyeballing rendered output (critical for tuning). **Cross-layer note:** the decision is render-layer logic, but the owner wants it in `moments.json` (intelligence-layer artifact). Resolution: the `ReframeDecision` model lives in `intelligence/ranking.py` (lower layer, so no reverse import) as an OPTIONAL field the intelligence layer never sets; the render layer populates it and re-writes `moments.json` after rendering. Manual (non-auto) methods report `median_count=0, max_spread=0.0` (no detection consulted). This is a deliberate, owner-requested second writer of `moments.json`.
  - **track** = full-bleed 9:16 window following the subject; **one-euro filter** (`min_cutoff=0.5, beta=0.007, d_cutoff=1.0`) smooths the crop path (adaptive: steady when still, responsive when moving). track keeps FULL HEIGHT (full body) → eyeline-upper-third / `crop_focal_y` stays deferred (needs face landmarks; don't fake it). ffmpeg time-varying crop = piecewise-linear `x(t)` expr, downsampled to `MAX_TRACK_KEYFRAMES=24`. **ffmpeg gotcha:** the crop y MUST be named (`crop=w:h:x='expr':y=0`) — a positional `:0` after the named `x=` is rejected ("No option name near '0'"). Commas inside the `if(lt(...))` expr survive because `x='...'` single-quoting protects them in the filtergraph parser.
  - **track follows the GROUP-bbox x-center** — correct for solo (the one box). Robust multi-person re-ID (track a specific person across a crowd) is deferred — see Future Enhancements.
  - **Fallback:** detection failed / zero subjects / `subjects.json` missing → `blur_pad` fs=1.0 (keep everything) + warning. `auto` never hard-fails.
- **`zoom_crop` method + `dual_output` (Session 9).** `zoom_crop` = full-bleed punched-in vertical, NO blur bars: `[0:v]scale={W·fs}:{H·fs}:force_original_aspect_ratio=increase,crop={W}:{H}[vout]` — cover a box scaled by `foreground_scale` (config `zoom_crop_decision.foreground_scale`, default **1.3**), then centre-crop to target. `fs=1.0` ≡ `center_crop`; `fs>1.0` zooms in further and crops more edge. Clamped via `clamp_foreground_scale` (min 1.0). **zoom_crop is NOT a decision-engine bucket** — it's a parallel forced framing for comparison, deliberately kept out of `decide_reframe` until the owner sees how it looks across real content (future tuning could promote it).
- **`dual_output: true` (shortform default).** Renders EVERY moment twice with identical captions/audio/timing/filenames: `src/output/shorts/auto/` (the auto decision per moment) + `src/output/shorts/zoom_crop/` (forced zoom_crop). 10 moments → 20 clips; `clip_NN.mp4` matches across folders for A/B comparison. Implemented in `run_render` via a `specs_for(out_dir, resolver)` closure called once per folder (auto resolver = the precomputed per-moment plan; zoom_crop resolver = a fixed `ReframePlan(zoom_crop, fs)`). `moments.json` records ONLY the auto decision (zoom_crop is parallel, not a decision). When `dual_output` is false (other modes) clips go straight to the base dir, unchanged. `dual_output` lives in the mode yaml (`ModeConfig` is `extra="allow"` — no schema change); `zoom_crop_decision` is a typed block in root `config.yaml`.
- **zoom_crop is subject-aware (Session 10): centres on the median group-bbox x, not the geometric centre.** `compute_zoom_crop_x` (social_formats) maps the subject's source-x through the cover-scale then clamps; `zoom_crop_x_for_window` (render/__init__) takes the per-moment window median; no detection → `None` → geometric-centre fallback (byte-identical filter string). Fixes a real bug: geometric centre put a 75%-right subject off-frame (1649px in a 1080-wide output). auto/ is unaffected — `zoom_crop_x` is read only in the ZOOM_CROP filter branch, and the dispatcher passes the resolver to the zoom_crop folder only. NOT in the auto decision engine (still parallel/forced).
- **Visual verification (Session 10, kkthedon):** MrBeast.mp4 centred; problem.mp4 (Sony FX30 solo) centred; GROW.mp4 centred on **8/10** moments — the 2 residual offsets are content-specific (multi-person group-bbox shifts and YOLO bbox variation across the window pulling the median off the speaker's face), NOT a math bug in `compute_zoom_crop_x`. auto/ and blur_pad output unchanged on all three (regression check passed). Accepted as v1 ship.
- **Iteration-cycle pain (Session 10) — Phase 6 priority bumped.** End-to-end `cme process` on GROW (701s 4K, full WhisperX + YOLO + render of 20 clips) measured **~6h20min wall time** this session, of which **~5h was a single mid-pipeline stall** (probable cause: Mac sleep — `pmset` defaults let display sleep idle the long-running CPU work; memory pressure on 8GB is the alternative hypothesis). A 20-min+ iteration cycle is blocking validation passes. Phase 6 (resume capability, cached workspace detection, perf) is therefore promoted to the immediate post-Phase-5 priority — perf work should land before/alongside the cinematic_longform and standard_longform pipeline builds, not after. Workaround for now: `caffeinate -i` long runs, or `cme reprocess` against a cached workspace when only intelligence/render changed.
- **Phase 4.5 status: CLOSED for v1 ship, revisit-possible flag (Session 10).** Math is provably correct (`compute_zoom_crop_x` derivation + 193 tests + worked example + the bug-demo's concrete numeric RED), so the residual GROW offsets are **not** a math bug — they're an input-quality issue (the median of YOLO person-bbox centres drifts off the face on multi-person moments / wide gestures). Re-open Phase 4.5 if any of these trigger:
  - **Trigger 1:** kkthedon's own content (gaming highlights, NYC vlogs, music process) shows consistent off-centering similar to the GROW residuals on real production renders.
  - **Trigger 2:** face-landmark detection lands (e.g. as part of Phase 5's vision work) — at that point wire face-centre x into `zoom_crop_x_for_window` as a v1.5 enhancement.
  - **Trigger 3:** user feedback or self-review identifies framing as the editorial weak point of shipped content.
  Re-open scope (so a future session doesn't have to rediscover): likely root cause of residuals = median **person-bbox** centre shifts on multi-person moments or wide gestures; enhancement path = **face-landmark detection replaces person-bbox median as the subject-x source; the centring math itself does not change** (`compute_zoom_crop_x` stays exactly as is).

### Performance — caching + parallelism (Phase 6, Session 12, kkthedon-approved)

- **Threads-only scheduler, NOT multiprocessing (CP1.5, evidence-backed).** `utils/parallel.py::run_graph` is a dependency-aware, lane-throttled scheduler on `ThreadPoolExecutor`. Threads (not `spawn` processes) because the heavy work releases the GIL during native compute (cv2 decode, numpy/librosa, CTranslate2/torch, ffmpeg subprocess) AND one process loads the cv2/av libavdevice dylibs ONCE — sidestepping both the dylib clash and the per-worker memory blow-up that would OOM 8GB. A minimal repro confirmed it: threads clean; `spawn` workers re-import cv2+av per worker (warnings repeat) and N× the memory. ProcessPool stays a documented future option for any pure-Python CPU-bound stage (none today). `performance.parallel_analysis: false` → max_workers=1 → same code path, sequential.
- **Three lanes (`config.yaml performance.*`):** LIGHT (librosa/opencv/aesthetics/hooks, cap=`max_workers`=4); HEAVY (whisper/subjects, `max_heavy_workers`=1 semaphore — one GB-class model at a time on 8GB); RENDER (per-clip ffmpeg, `max_render_workers`=2 — videotoolbox-session-safe). Deadlock-free: a task is submitted only once its deps complete (no worker blocks on another task). Errors isolated: a failed stage's dependents skip, the batch survives (SPEC §9). `hooks` depends on `whisper` ONLY when whisper re-runs; otherwise it reads the cached `transcript.json` (workspace-as-contract).
- **Per-artifact caching (two-layer, CP1).** Layer 1 — `ingest` discovers an existing workspace by the **resolved** source path in metadata.json and reuses it when `source_unchanged` (identity = resolved path + size + mtime_ns, stored in `cache_manifest.json` under `__source__`). Layer 2 — `run_analysis` checks each stage's `artifact_fingerprint(source, its config section)` against the manifest; only stale stages re-run (`AnalysisOutputs.from_cache` records hits), and a stage whose dependency re-ran is force-invalidated via the deps∩to_run propagation. `--fresh` bypasses both. Manifest writes are atomic (temp + `os.replace`); a failed stage leaves its entry stale so the next run retries it. **Fast composite key, NOT a content hash** (too slow on 4K). *Known edge case (not built):* a corrupt artifact whose config is later reverted to a matching fingerprint could false-hit; mitigation if ever observed = atomic writes for analysis artifacts (v1.5 hardening).
- **New CLI:** `cme reprocess <ws> --mode X` re-runs intelligence + render from cached analysis (skips analysis entirely) — the fast scoring/render iteration path. `cme workspace list / info / clean [--dry-run]` for status + per-artifact fresh/stale inspection + retention cleanup (`parse_age` 7d/24h/30m; only `run_*` dirs; recency protects active runs). `keepawake.py` wraps process/reprocess with `caffeinate -w PID` on macOS (no-op elsewhere) — fixes the Session-10 ~5h idle-sleep stall. Rich progress: one row per analysis stage with a live elapsed timer (hang diagnostic) + a render clip counter, driven by scheduler `on_event`.
- **Display gotcha:** in `console.print`, a bare `[a, b]` is parsed as Rich markup and silently dropped — `workspace list` lost its status flags until switched to `(a, b)`. Caught only in the live terminal (CliRunner-captured output parses markup differently, so the unit test passed). Lesson: dynamic text in `console.print` must avoid bare square brackets or be escaped.
- **Cache correctness verified:** fresh vs cached `moments.json` byte-identical except `generated_at`; rendered clip sizes identical. Render is uncached BY DESIGN (it produces the output files; `reprocess` re-renders).

### Branding architecture decision (Phase 6 close)

- The watermark overlay and `caption_bold.ttf` font are kept as code paths but the asset files are **intentionally absent**.
- Branding finalization will be handled by **Descript** (or whatever final-polish tool the user chooses) in post-production, **NOT** by this engine.
- This engine outputs functionally-correct clips with **Helvetica** system font and **no watermark**. Visual branding is added downstream.
- The branding code (watermark overlay logic, custom font loading) is marked as **REMOVAL CANDIDATE for v1.5 cleanup** once Descript integration is confirmed. Do **not** actively maintain or extend this branding code.
- If a future session needs to remove the branding code entirely, the work touches: `render/ffmpeg_pipeline.py` (overlay filter logic), `render/social_formats.py`/`render/subtitles.py` (font loading), and the `branding/*` asset references in the mode configs.
- The two per-clip "missing asset" notices (watermark, caption font fallback) were downgraded WARNING→DEBUG at Phase 6 close (Session 12) since the absences are expected; the genuinely-serious "no caption font found at all" case stays WARNING.

**Status:** kept for v1, marked for removal in v1.5+ once the downstream polish workflow is established.

### Render layer (Phase 7, Session 13, 2026-05-30, kkthedon-approved) — standard_longform = Descript-prep

- **Architectural decision: the engine is the "intelligent prep tool" for Descript.** Across ALL three pipelines the engine does analysis + selection + cutting; Descript handles final polish (captions, branding, thumbnails, color grade). Phase 7 implements this for standard_longform; Phase 8 (cinematic) inherits it. The shortform caption/watermark burn (Phase 4/4.5) is now DUPLICATE work with Descript — a v1.5 cleanup will strip it (NOT in Phase 7, to avoid breaking working shortform). Reinforces the Phase-6 branding REMOVAL-CANDIDATE note.
- **Isolated longform render path (`render/longform.py`) shares ZERO code with shortform.** Reuses the videotoolbox→libx264 fallback PATTERN (duplicated ~6 lines, not imported) and the `RenderFormat` DTO, but does NOT touch `render/__init__.py`, `ffmpeg_pipeline.py`, `social_formats.py`, or `subtitles.py`. Rationale: shortform is FROZEN; a separate module structurally cannot regress it. The ffmpeg command is a plain `-vf scale=W:H` cut (NO `-filter_complex`), so it cannot carry a reframe/caption/overlay filter. Verified post-build: shortform end-to-end unchanged on Problem.mp4 (dual auto/+zoom_crop/, A/B filename invariant intact).
- **Window scoring needed NO code change.** `pacing_engine.build_pacing_plan` already reads `preferred_duration_sec or max_segment_sec`; setting `standard_longform.yaml pacing.preferred_duration_sec: 300` makes the existing sliding-window scorer pick the best 300s window directly (3–10 min = edit that value). The "60s hardcode" was ALWAYS shortform-only (`shortform.yaml` + render `PLATFORM_MAX_CLIP_SEC`). Phase 7 cuts the SINGLE best window (`select_best_window` = top moment, else best below-threshold fallback); multi-window stitching = Phase 7b. Window clamps to source length when source < target (verified: Problem.mp4 254s < 300s → whole-source cut, score 0.773).
- **standard_longform default scoring is audio-driven.** `score_standard` exposes only `audio_energy` (librosa energy) + `conversational` (speech_intensity) by default; `transcript_quality` joins with `--transcribe`; `pacing_consistency` + `narrative_continuity` are UNAVAILABLE (TwelveLabs/Phase 8) → redistributed. So default window selection = "liveliest window," not narrative. Honest v1 behavior; revisit when semantic signals land.
- **Thumbnail FRAME composite = composition_score + lighting_quality + sharpness (`render/thumbnails.py`).** `color_harmony` (Hasler-Süsstrunk colorfulness) is DELIBERATELY EXCLUDED — it's a saturation proxy, not thumbnail quality; the three kept signals correlate most with a strong still. A-PRIORI choice, NOT validated on real output (color_harmony stays in `aesthetics.json` — re-add via the `THUMBNAIL_METRICS` tuple if real candidates look wrong). Top-N frames in the window by composite, `min_gap_sec=5.0` for distinctness; no aesthetics data → deterministic middle-of-window frame (never random). Reads cached `aesthetics.json`.
- **Thumbnail TEXT ranking = mean WhisperX confidence (`intelligence/thumbnail_text.py`).** Per quotable span (from `hooks.json` — the quotable_dialogue source) overlapping the window, take the highest-mean-confidence 3–7 word contiguous run (≥`MIN_WORD_CONFIDENCE=0.3`, reused from scoring.py), rank phrases by mean confidence, top-N. DELIBERATELY no hook-token bonus, no length penalty — don't double-optimize: moment selection already handles viral-hook attention at the window level; thumbnails want clear ENUNCIATION (kkthedon, Flag 1). **Tuning dial:** if real suggestions feel weak (Problem.mp4 gave mid-sentence fragments like "to the ND"), add a length penalty / hook-token bonus / switch to median confidence here. Tunable, not fundamental.
- **`thumbnail_text_status` 4-value scheme:** `ok` / `transcript_unavailable` (no `--transcribe`) / `no_quotable_phrases_found` (transcript present, nothing qualified in window) / `not_generated` (`MomentsResult` default for modes that never extract, e.g. shortform). Lets Descript/automation distinguish empty-no-transcript from empty-no-content.
- **moments.json: 3 additive optional fields** (`thumbnail_candidates: list[str]=[]`, `thumbnail_text_suggestions: list[str]=[]`, `thumbnail_text_status: str="not_generated"`), mirroring the Phase-4.5 `ReframeDecision` additive pattern. **Schema-consistency-across-modes chosen over shortform byte-equivalence (kkthedon, Flag 2):** shortform moments.json now carries these keys at defaults (values don't affect shortform consumers). Old Phase-4.5/6 moments.json deserialize cleanly (defaults supplied) — backward-compat guard `test_pre_phase7_moments_json_loads_with_defaults`. Written by the render layer (a deliberate second writer of moments.json, like the reframe diagnostic).
- **Output tree `src/output/longform/standard/`** (config `output.base_dir`, NOT hardcoded) — parallel to the future `src/output/longform/cinematic/`. D4 config edits (4 lines, no `config.py` schema change — mode-yaml extras ride `ModeConfig` `extra="allow"`): added `preferred_duration_sec: 300`, `base_dir → longform/standard`, `thumbnail_count 5→3`, `thumbnail_text_count: 3`.
- **CLI dispatch:** `process`/`reprocess` route `standard_longform`→`run_longform_render`, `shortform`→`run_render` (unchanged), `cinematic_longform`→graceful "not built (Phase 8)" notice. `keep_awake` wraps both; Phase-6 caching is automatic (analysis is mode-agnostic, upstream of dispatch — same source = same cache across modes, verified on Problem.mp4: shortform run cache-hit the standard run's analysis).

### Descript handoff (Phase 9, Session 15, 2026-06-01, kkthedon-approved) — automated polish

- **DISCIPLINE (persists to all future sessions):** All Descript-related work MUST reference `docs/descript_api_research.md` AND verify capabilities against the active Descript MCP tools before implementation. Applies to Phase 9 and all subsequent Descript-touching work. (Phase 9 had NO Descript MCP tools available → called the REST API directly via `requests`; no Python SDK exists either.)
- **Architecture: Interpretation A only, synchronous, fail-loud.** The engine produces the final cut; Descript polishes only. Single file in → single file out. Flow per `--polish`: `upload_to_descript` (3-step signed-URL import) → [cinematic: `import_media` for the music bed] → `_resolve_composition` → ONE combined `run_underlord` prompt → `wait_for_job` → `publish_project` → `download_polished`. New module `src/descript/{auth,handoff}.py` (deviation from SPEC §1 folder tree, owner-approved; Phase 7/8 already extended the `output/` tree, precedent set). No frozen render code touched.
- **CORRECTION — "combined prompt is cheaper" is NOT documented.** The research doc has no claim that one combined agent prompt costs fewer credits than N sequential calls (research L1235); cost scales with operations performed and is reported post-hoc via `ai_credits_used`. We still send ONE combined prompt because it is the **documented recommended one-shot pattern** (research §4.2 L535, Pattern C L1489) — a *correctness* reason (no conversational follow-up), not a proven cost saving. Sample cost: "Add studio sound and captions" = 32 credits (L1415); fuller polish ≈ 30–60 (L1491).
- **CORRECTION — color: Descript has NO LUT/grading, but DOES have "Color Adjustments."** The research doc conflated "color grading/LUTs" (a real gap — open canny LUT request, L72/L101) with all color. Live-verified that **Color Adjustments** (exposure/contrast/white-balance sliders + filter presets) is a real Descript app feature ([help.descript.com/.../10327606944653](https://help.descript.com/hc/en-us/articles/10327606944653-Color-adjustments)), but it is a **manual Scene-Editor Effect**; whether **Underlord** can invoke it from a prompt is **UNDOCUMENTED** (no example prompt in any primary source). Governing principle "anything Underlord can do in-app the API can do" (L50) made it plausible → **CONFIRMED at live test 2026-06-02**: Underlord applied real values from the prompt (exposure +0.05, contrast +0.1, highlights -0.05, shadows +0.1, saturation +0.05, neutral WB), so "Apply color adjustments to balance exposure, contrast, and white balance" stays in the longform prompts — proven, not best-effort. The engine's Sony Cool 709 LUT stays baked in (cinematic); per owner, Descript color adjustments are applied ON TOP of the cinematic grade — verify the combined look once the cinematic live test runs. `docs/descript_api_research.md` §1.1 row updated with Studio Sound's file-level behaviour (one file/project → whole video enhanced in one op).
- **CHANGE — cinematic music is USER-PROVIDED, not stock.** Overrides the original locked "stock-only, no upload" decision. The bed (`src/branding/music/cinematic_bed.mp3`, gitignored under branding) is imported as a 2nd project media (`import_media`, supported per research L291/L331) and placed by the prompt ("add the imported music track … ducked under dialogue"). Music-via-agent is "inferred" (L559) → best-effort, verify live. Cinematic music-file existence is checked at the TOP of `polish_video` BEFORE any HTTP (fail-loud, no wasted credits/minutes).
- **Two-level job success (critical):** `GET /jobs/{id}` `job_state=="stopped"` is terminal but NOT necessarily successful — `wait_for_job` also requires nested `result.status=="success"`, else raises (research L955–971). `composition_id` is required for publish (L621) and resolved via `GET /projects/{id}`; a single-video import is expected to yield ONE composition → we take the first; **live-verified a single-video import yields exactly ONE composition** (research L308). Signed `download_url` carries its own auth in the URL → no Bearer on the download GET (research §5.1 L600), mirroring the upload PUT leg.
- **Polish is longform-only (single file in → single file out):** standard/cinematic polish the SINGLE rendered cut; standard's prompt adds YouTube captions (the engine cut is uncaptioned), cinematic imports the music bed. Output: `<base>/polished/{date}_{src}_{mode}_polished.mp4`. Idempotent: new Descript project per run, no dedup. `DescriptSettings` (config.py) is per-mode, disabled by default; `--polish` on `process` + `reprocess`, inside the existing `keep_awake()`. (Shortform polish was built then removed — see the late-correction addendum below.)
- **Tests:** 48 mocked-HTTP tests (`tests/test_descript.py`) — FakeResponse queue + monkeypatched `requests.request` + deterministic clock; assert OUR request-building/parsing/error-mapping, never "a mock was called". NO live network in CI; live API verification is separate.
- **Deferred (Phase 9 explicit non-scope):** thumbnail retrieval (manual via app; Phase 10 if proven), webhook callbacks, async job submission, multi-token/multi-drive, sequential per-op prompts, cost/credit monitoring, parallel Descript jobs, b-roll, custom music beyond the single cinematic bed, retry beyond 429-Retry-After (max 3) + timeout. 429 honours `Retry-After` then raises `RateLimitError` after 3 retries.

#### Live verification + late corrections (Session 15 cont., 2026-06-02)

- **`add_compositions` is REQUIRED at import — the bug live verification caught (mocks couldn't).** Importing with `add_media` alone leaves the clip in the project media POOL with an EMPTY composition (`duration: 0`); Underlord then reports *"this composition has no media content"* (4 credits, no real work) and publish fails *"Job failed unexpectedly."* Fix: `_import_media` sends `add_compositions: [{name, clips:[{media: key}]}]` on the new-project path so the clip lands on the timeline (research §2.5 L291 / §2.11 L336). Music-to-existing-project stays a pool add (Underlord places it). The two-level `wait_for_job` check correctly caught the publish failure and halted fail-loud — **the handoff code was right**; diagnosis came from reading `agent_response` + `GET /projects` (one EMPTY composition — not the multi-composition disambiguation we first feared, so no re-resolution/ordering change was needed).
- **Color Adjustments CONFIRMED Underlord-accessible** (resolves the CP1 open question) — see the color bullet above. **Per-video cost ≈ 14 AI credits** (Eye Contact 10 + Studio Sound + Color Adjustments), the SAME for a 60s shortform clip and a 254s standard cut → cost is **per-effect, not strongly duration-scaled**; + ~1 media-minute per import. `polish_video` now logs `ai_credits_used`/`media_seconds_used`.
- **ARCHITECTURE: shortform `--polish` REMOVED (kkthedon, 2026-06-02).** Shortform clips are cut FROM polished longform output and inherit polish through the cut → polishing them again is redundant paid work. `cme process|reprocess --mode shortform --polish` now ERRORS with guidance ("polish longform first, then cut shortform from it"). Removed: shortform `descript:` block, `--polish-clips` flag, `DescriptSettings.default_clip_selection`, and `select_polish_targets`/`_clip_rank`/`build_polished_path`-rank logic. Polish is longform-only, single-file in/out. Tests dropped 56→48; shortform-error path added.
- **PROCESS LESSON (why this slipped to a late correction):** it should have been caught at CP1 scope-locking. The "always check `docs/descript_api_research.md`" discipline did NOT help here because the issue was **production-workflow understanding** (shortform sources its footage FROM longform), not API capability. **Lesson for future CP1s: validate the END-TO-END workflow — where does each pipeline's INPUT come from? — not just per-pipeline API mechanics.**
- **Live status at commit:** shortform `--polish` errors cleanly (verified, free). standard_longform `--polish` PASS — 14 credits, 247 MB cut → 350 MB polished (Studio Sound + Eye Contact + Color Adjustments; the live PASS run also added captions — REMOVED in 9.1 below), ~4.5 min. cinematic_longform built + unit-tested but **live-DEFERRED** until the owner drops `src/branding/music/cinematic_bed.mp3` in place (the run fail-loud-checks for it before any HTTP).
- **Caption policy (Phase 9.1 update, 2026-06-02):** Captions are **shortform-only**. The engine burns captions for shortform via ffmpeg. Longform and cinematic content stays **caption-free** — viewers of vlog/cinematic content typically have sound on and don't need burned-in captions; for YouTube uploads, YouTube's auto-caption system provides them as a toggle. So the standard_longform Descript prompt no longer requests "Add styled captions…" (cinematic never did). Enforced by `test_longform_prompts_have_no_captions`.

### Distribution (out of scope for THIS engine)

- Auto-upload to platforms is a separate system. This engine produces files.
- F8/F9 real-time clipper is a separate project. Don't modify it.

---

## Things Tried That Didn't Work

*(Empty at project start. Fill in as you build.)*

### Format for entries:

```
**[Date] — What you tried, briefly**
What you tried: ...
Why it failed: ...
What worked instead: ...
```

### Examples (delete after first real entry is added):

**2026-05-24 — Loading Whisper `medium` model on M1 8GB**
What you tried: Bumping Whisper from `small` to `medium` for better transcription on music process content.
Why it failed: OOM on 8GB unified memory when running OpenCLIP simultaneously.
What worked instead: Stay on `small`. Accuracy difference is marginal for our content type.

---

## Library Quirks Discovered

*(Empty at project start. Fill in as you hit them.)*

**CTranslate2 (faster-whisper / WhisperX) — no Apple Metal backend**
Behavior: CTranslate2 has no Metal/MPS backend (confirmed still true 2026), so the faster-whisper transcription step runs CPU-only on M1 — `device="auto"`/`"mps"` silently falls back to CPU. `compute_type="float16"` is effectively a CPU no-op; use `int8`.
Workaround: Configure transcription `device=cpu`, `compute_type=int8`. The wav2vec2 alignment step is a separate torch model and DOES use MPS — set alignment `device=auto`.
Reference: SYSTRAN/faster-whisper issue #515

**PyTorch MPS — wav2vec2 alignment conv1d fails on long audio ("Output channels > 65536") (Phase 3)**
Behavior: the wav2vec2 forced-alignment step (`whisperx.align`) runs on MPS and its conv1d raises `NotImplementedError: Output channels > 65536 not supported at the MPS device` on long audio segments. Short clips (the 30s `real_speech_sample`) align fine on MPS; a 10-min source triggers it. faster-whisper transcription (CPU) is unaffected — only the GPU alignment pass fails. Discovered running a 10-min clip through `cme process --mode shortform`.
Workaround: implemented in `whisper.py::align_transcript` — try the configured device, on `NotImplementedError`/`RuntimeError` warn + retry the whole alignment on CPU (SPEC §9). Do NOT "revert torch" as the message's other suggestion implies; `PYTORCH_ENABLE_MPS_FALLBACK=1` also works but is global + must be set before torch init, so we handle it in-code instead. Force a device via `CME_ALIGNMENT_DEVICE` if needed.
Verified (Session 4): forcing one 600s segment reproduces it and the fix recovers — `CAUGHT on MPS: NotImplementedError :: Output channels > 65536 not supported at the MPS device...` then `WARNING alignment on mps failed (...); retrying on CPU` → `aligned ok on device=cpu`. IMPORTANT: it's **segment-length** dependent, not clip-length. A real 10-min clip (MrBeast.mp4) splits into 323 short segments (~1.9s) and aligns fine on MPS WITHOUT the fallback; the limit is only hit when WhisperX emits a long segment (continuous speech / coarse VAD). So the fallback fires on some content, not all long clips — but the original crash (`real_longer_sample.mp4`) had such a segment.

**Session-0 pyproject.toml — unverified exact pins were unsatisfiable**
Behavior: The initial AI-generated pins (never `uv sync`'d) contained a non-existent package (`pyscenedetect`; the real PyPI name is `scenedetect`) and mutually incompatible exact pins (`twelvelabs==0.2.7` hard-pins `pydantic==2.4.2`, conflicting with `pydantic==2.8.2`). uv also defaulted to the newest system interpreter (Python 3.14), which lacks darwin wheels for the heavy deps.
Workaround: Switched every pin to compatible-release ranges (`>=X,<next-major`) and let uv resolve one coherent set; pinned the interpreter to 3.11 via `.python-version`; capped `requires-python` to `>=3.11,<3.13`. Exact resolved versions are captured in `uv.lock` (committed) for reproducibility.

**uv build — `src`-as-package layout needs an explicit backend mapping**
Behavior: With no `[build-system]`, uv's default build exposed `src/`'s CONTENTS as top-level modules (so `import config`/`import cli` worked but `import src` did not). The `cme` console script (`src.cli:app`) then died with `ModuleNotFoundError: No module named 'src'`, even though pytest passed (pytest prepends the project root to sys.path, so `src` resolves there).
Workaround: Declare hatchling and map the directory to the `src` package:
`[build-system] requires=["hatchling"]`, `build-backend="hatchling.build"`; `[tool.hatch.build.targets.wheel] packages=["src"]`. Then `from src...` works in tests AND the installed script. Re-run `uv sync` after the change.
Reference: SPEC §1 mandates the `src/` package layout.

**pre-commit mypy hook — isolated env must mirror type-relevant deps**
Behavior: pre-commit runs the mypy hook in an isolated env containing ONLY the packages in `additional_dependencies`. If a type-relevant dep is missing (e.g. typer), `--ignore-missing-imports` turns it into `Any` and `--strict` then emits false positives like "Untyped decorator makes function untyped" on `@app.command()` — errors that do NOT appear in `uv run mypy src/`.
Workaround: Keep the mypy hook's `additional_dependencies` in sync with the project's type-relevant deps (typer, rich, pydantic, pydantic-settings, types-*). Loosen version pins (e.g. `pydantic>=2.0,<3.0`) to avoid drift. Never paper over with broader `--ignore-missing-imports`.
Reference: .pre-commit-config.yaml mypy hook.

**cv2 + av — clashing libavdevice dylibs on macOS (Phase 2)**
Behavior: `import cv2` prints `objc[..]: Class AVFFrameReceiver is implemented in both .../cv2/.dylibs/libavdevice... and .../av/.dylibs/libavdevice...` to stderr (both opencv-python and `av`, pulled in via the torch/whisperx stack, ship their own ffmpeg dylibs). The warning says it "may cause spurious casting failures and mysterious crashes." Single-process decode (VideoCapture, scenedetect) works fine; the risk is under `multiprocessing` spawn where each worker re-imports cv2.
Workaround: Keep the analysis layer **sequential** for now — `run_analysis` runs modules in-process. Defer the SPEC §5 / CLAUDE.md-rule-5 ProcessPoolExecutor parallelism to Phase 6, and test it explicitly when added. It's a native stderr print, not a Python warning, so pytest's `filterwarnings` can't suppress it (pytest captures it per-test instead).

**librosa.load → audioread imports deprecated stdlib aifc/sunau/audioop (Phase 2)**
Behavior: `librosa.load` touches the `audioread` backend, whose `rawread` imports the stdlib `aifc`/`sunau`/`audioop` modules — all deprecated on Python 3.11 (removed in 3.13) — emitting `DeprecationWarning`s during tests.
Workaround: Scoped `filterwarnings` ignores in `[tool.pytest.ini_options]` for exactly those three messages. Not our code; do NOT swap to `soundfile` directly (it's only a transitive dep — adding it as a direct dep is a stop-and-ask). Revisit when the audio stack updates for 3.13.

**WhisperX uses pyannote for VAD (not diarization) — benign version warnings (Phase 3 smoke)**
Behavior: with `vad_filter: true`, WhisperX loads a **pyannote** segmentation model for voice-activity detection (logs `>>Performing voice activity detection using Pyannote...`) and emits benign warnings: `Model was trained with pyannote.audio 0.0.1, yours is 3.4.0` and the same torch-1.10-vs-2.5.1 note as wav2vec2. So pyannote IS pulled in (for VAD), even though DIARIZATION is off (no HF token needed for the bundled VAD model). Also a torch `weights_only=False` `FutureWarning` from lightning's checkpoint load — all informational, the run exits 0.
Workaround: none needed; runs fine. Do not "revert torch/pyannote" as the warnings suggest.

**whisperx 3.4 — API lives in submodules, top-level re-exports are `(*args, **kwargs)` (Phase 2)**
Behavior: `inspect.signature(whisperx.load_model)` shows `(*args, **kwargs)`; the real signatures are on the submodules: `whisperx.asr.load_model(whisper_arch, device, compute_type, asr_options, language, vad_options, ...)`, `whisperx.alignment.load_align_model(language_code, device)` → `(model, metadata)`, `whisperx.alignment.align(segments, model, metadata, audio, device, ...)`, `whisperx.audio.load_audio(file, sr)`. `model.transcribe(audio, batch_size, language)` returns `{"segments":[...], "language":...}`.
Workaround: beam_size/condition_on_previous_text go in `asr_options`; VAD min-silence in `vad_options`. Set transcription `device=cpu` (CTranslate2) and alignment device separately (`auto`→mps).

### Format for entries:

```
**Library — Quirk**
Behavior: ...
Workaround: ...
Reference: <link if applicable>
```

### Examples (delete after first real entry is added):

**faster-whisper — `language` param**
Behavior: Setting `language="en"` is faster than letting it auto-detect on each call, especially for short audio segments.
Workaround: Always pass `language` explicitly in config.

**ffmpeg on M1 — hardware acceleration**
Behavior: Use `-c:v h264_videotoolbox` for hardware-accelerated H.264 encoding on M1, ~3x faster than software encoding.
Workaround: Default to videotoolbox in `src/utils/ffmpeg.py`, fall back to software if videotoolbox unavailable.

---

## TwelveLabs Query Phrasings That Work

*(Empty at project start. Fill in as you tune.)*

Queries are mode-specific. Track which phrasings actually return useful results for each content mode.

### shortform mode

*(Add working queries here. Format: query → what it catches well → what it misses)*

Example placeholders to replace:
- `"high energy reaction or hype moment"` — catches loud reactions, misses subtle smirks
- `"funny or laughing moment"` — works well for genuine laughter, weak on jokes that land via deadpan delivery

### cinematic_longform mode

- `"emotional or vulnerable moment"` —
- `"visually striking composition"` —
- `"transition between activities"` —

### standard_longform mode

- `"conversational peak or quotable line"` —
- `"narrative shift or topic change"` —

---

## Tuned Weight Values

*(Defaults live in `content_modes/*.yaml`. Track tuning here with WHY.)*

### shortform.yaml

```
# Initial defaults from SPEC.md:
# speech_intensity: 0.25
# audio_peaks: 0.20
# viral_hooks: 0.20
# emotional_reactions: 0.15
# quotable_dialogue: 0.10
# fast_motion: 0.10
```

**Tuning log:**

- **2026-05-26 (Phase 3, first real output):** `real_speech_sample.mp4` (30s) → shortform, `--no-twelvelabs`. All 6 signals available (no redistribution). The single clamped 30s window scored **0.254** vs `score_threshold: 0.55` → **0 moments** (correct: honest empty, threshold NOT lowered). This is the first real datapoint to tune against — do NOT adjust weights/thresholds from one 30s clip; gather more real footage first (especially >60s sources so the window actually slides). Likely contributors to the low score on this clip: 0 hooks found, sparse peaks, moderate speech energy. Pacing/clip length is locked at 60s so a 30s source can yield at most one (clamped) candidate.
- **2026-05-27 (Phase 3, first >60s real output):** `MrBeast.mp4` (10-min, 2034 words, 25 hooks, 167 quotables, 16 peaks, 297 scene cuts) → shortform. 542 windows, scores 0.250–0.361 (median 0.310) — **near-zero spread**, best window 503–563s = **0.361** → 0 moments (fallback returns top-5). Best-window per-signal MEANS (raw, pre-weight): speech_intensity 0.481, quotable_dialogue 0.475, viral_hooks 0.337, audio_peaks 0.315, fast_motion 0.258, emotional_reactions 0.248. **Diagnosis:** composite = weighted avg of windowed-means; means of spiky normalized signals top out ~0.48, so composite ceiling ~0.36 — this is the Phase 3.5 mean-aggregation problem in real data, NOT a weight-tuning issue. Don't tune weights until aggregation is reworked.
- **2026-05-27 (Phase 3.5, percentile aggregation):** same `MrBeast.mp4`, now `window_aggregation: percentile/90`. Best window 502–562s = **0.744** (was 0.361). Window scores spread to **0.46–0.74** (median 0.622); **375/542 windows ≥ 0.55** → 10 moments kept after diversity (min_gap 30s / max 2/min). Best-window p90 signal values: quotable 1.00, viral_hooks 0.896, audio_peaks 0.800, speech 0.676, fast_motion 0.584, emotional 0.512. Threshold held at 0.55 (now permissive on this dense video — below the 0.62 median; revisit on calmer content). Weights still NOT tuned — wait for varied real footage; the aggregation fix was the structural lever, weights are fine for now.

### cinematic_longform.yaml

```
# Initial defaults from SPEC.md:
# openclip_aesthetic: 0.30
# emotional_moments: 0.20
# lighting_quality: 0.15
# visual_composition: 0.15
# pacing_curve: 0.10
# soundtrack_sync: 0.10
```

**Tuning log:**

*(empty)*

### standard_longform.yaml

```
# Initial defaults from SPEC.md:
# transcript_quality: 0.30
# pacing_consistency: 0.25
# narrative_continuity: 0.20
# conversational: 0.15
# audio_energy: 0.10
```

**Tuning log:**

*(empty)*

---

## Performance Benchmarks Observed

*(Empty at project start. Fill in as you measure.)*

Track actual times on real footage. Helps catch regressions and tune expectations.

### Format:

```
**[Date] — [Step] on [input size]**
- Hardware: M1 8GB / 16GB
- Mode: shortform / cinematic_longform / standard_longform
- Input: 60 min 1080p stream VOD
- Time: 8m 14s
- Notes: ...
```

### Observed (Phase 2, synthetic 60s/1080p fixture, M1)

**2026-05-25 — analysis layer on the 60s test fixture (sequential)**
- standard_longform (no transcription): ~11s total — librosa ~3s, opencv motion+scenedetect ~5s, aesthetics ~3s.
- shortform (+ WhisperX): ~58s total, including a one-time 360MB wav2vec2 alignment-model download. Steady tone → 1 hallucinated word; transcribe runs on CPU (int8), alignment on MPS.
- Full pytest suite (57 tests, 1 skipped): ~53s with coverage (dominated by the opencv/aesthetics integration decodes). These are NOT representative of real 60-min footage; recorded only as a Phase-2 sanity baseline.

### Observed (Phase 6, real_speech_sample.mp4 = 30s real speech, M1, shortform)

**2026-05-30 — caching + parallelism end-to-end**
- Fresh run (`--fresh`): **43.88s** wall, user-time 139s ≫ real → genuine multi-core execution. Analysis wall ~23s vs sum-of-stages ~41s → the parallel scheduler alone is ≈ **1.8×** on the analysis layer (light stages overlap the long `whisper` stage). Render ~18s.
- Cached re-run (no `--fresh`): **18.48s** wall; 6/6 stages cache hits (0 re-run). Analysis ~23s → ~0.5s ≈ **46× on the analysis layer** (the part the cache owns).
- **End-to-end speedup = 2.37×** (43.88→18.48) — ~91% of the THEORETICAL CEILING for this fixture (43.88/18 ≈ 2.6×): render is ~40% of a 30s clip and is uncached by design, so end-to-end is render-bound.
- **Math reality:** a 5× end-to-end ratio requires analysis to DOMINATE total time — true on long videos, NOT on a 30s clip. For the 6h GROW render (analysis = the overwhelming majority of wall time), cached re-runs should yield **10–20×+** end-to-end once long-form modes ship — the real production iteration win. The 30s-fixture "5× gate" was set on a wrong assumption (analysis-always-dominates) and was overridden by kkthedon after this measurement. The cache invariant (identical `moments.json` fresh vs cached) is the real proof the cache is correct.
- Full suite after Phase 6: **227 passed / 2 skipped**, 92% coverage, zero pre-existing tests modified.

### Initial targets (from SPEC.md, for reference):

- 60 min 1080p input, M1: ~25-45 min total end-to-end
- Whisper small: 8-12 min
- OpenCLIP sampled: 3-6 min
- TwelveLabs upload+index: 8-15 min (parallel)

---

## Brand Aesthetic Decisions (kkthedon)

*(Define these as you lock the visual identity. Update when you tune.)*

### Cinematic longform style

- **Color grade direction:** *(undefined — define when cinematic mode is built)*
- **LUT file:** `branding/luts/kkthedon_cinematic.cube` *(not yet created)*
- **Transition style:** *(undefined)*
- **Pacing baseline:** *(undefined)*
- **Font choices:** *(undefined)*

### Shortform style

- **Caption font:** *(undefined)*
- **Caption position:** *(undefined)*
- **Watermark placement:** *(undefined)*
- **Zoom effect intensity:** *(undefined)*
- **Energy baseline:** brooklyn dumbass — fast, raw, no overthinking

### Standard longform style

- **Intro length:** *(target under 10 seconds)*
- **Outro:** *(undefined)*
- **Lower-third style:** *(undefined)*

---

## Content Mode Selection Heuristics

When to use which mode:

- **shortform** → any source that needs to become TikTok/Reels/Shorts clips
- **cinematic_longform** → music process videos, artist documentaries, anything that needs visual storytelling
- **standard_longform** → stream VODs, gameplay highlights, casual vlogs

Multi-mode runs: shortform clips can be generated from ANY source via `--mode shortform`. Add `--mode standard_longform` or `--mode cinematic_longform` in the same command to produce both outputs from one analysis pass.

---

## Environment Setup Quirks

*(Things specific to YOUR machine that future sessions need to know.)*

### M1 Mac specifics

- **PyTorch MPS backend** for OpenCLIP — set `device="mps"` in OpenCLIP code, fallback to `"cpu"` if unavailable.
- **`CME_ALIGNMENT_DEVICE` env var** overrides the wav2vec2 alignment device (`auto` | `mps` | `cuda` | `cpu`). Default (unset) = the config value `transcription.alignment.device` (`auto` → MPS if available, else CPU). On a GPU-op failure the engine auto-falls-back to CPU (see Decisions Made + the MPS conv1d Library Quirk). Set `CME_ALIGNMENT_DEVICE=cpu` to force CPU (e.g. debugging, or pre-empting the MPS long-audio limit); `=mps` to force-test the GPU path. faster-whisper transcription remains CPU-only regardless.
- **Homebrew ffmpeg** — installed via `brew install ffmpeg`, location: `/opt/homebrew/bin/ffmpeg`
- **Python 3.11** — installed via `uv`, project uses `.venv` managed by uv
- **uv resolved stack (2026-05-24, verified working on M1):** Python 3.11.15; torch 2.5.1 / torchaudio 2.5.1 / torchvision 0.20.1 with **MPS available + built = True**; whisperx 3.4.5, faster-whisper 1.2.1, ctranslate2 4.4.0, twelvelabs 1.2.4, pydantic 2.13.4, numpy 2.4.6. 148 packages, all wheels except 4 tiny pure-Python sdists (docopt/julius/antlr4 + the project). `uv sync` ~15s warm.
- **uv.lock IS committed to git** — it is the reproducibility layer; `.gitignore` does NOT exclude it. Never gitignore the lockfile. uv itself installed via `brew install uv` (v0.11.16).
- **Stray `VIRTUAL_ENV`** — the shell profile exports `VIRTUAL_ENV=/Library/Frameworks/Python.framework/Versions/3.10`, which pollutes `uv pip list` / `uv run`. Prefix uv commands with `env -u VIRTUAL_ENV`, or call `.venv/bin/python` directly.

### TwelveLabs

- **API key location:** `.env` file, env var `TWELVELABS_API_KEY`
- **Index ID:** `.env` file, env var `TWELVELABS_INDEX_ID`
- **Index name:** *(set after first creation)*
- **Free tier limits:** check at start of each month — quota resets monthly

---

## Workflow Notes

### Starting a new Claude Code session

1. Open the project in Claude Code
2. Filesystem MCP, Memory MCP, Context7 MCP should auto-load
3. Tell Claude Code: "Read SPEC.md, CLAUDE.md, and KNOWLEDGE.md. Confirm phase status from git log. Then we begin."
4. Check git log to see what's committed
5. Confirm which phase is next per SPEC.md Section 14

### Ending a session

1. Commit any pending work
2. Update KNOWLEDGE.md with anything learned
3. End the session — do not extend into the next phase

---

## Open Questions

*(Things you haven't decided yet but will need to.)*

- **(Phase 3 gate) WhisperX transcription quality on real kkthedon speech is UNVERIFIED as of end-Session-2.** Phase 2 only exercised the synthetic 440 Hz tone (which makes Whisper hallucinate the single token "you"), so transcript/word-timing accuracy on real speech has not been validated. **Run a real speech sample through `cme process ... --mode shortform` manually and eyeball `transcript.json` BEFORE Phase 3 scoring leans on transcript-derived signals** (viral_hooks, quotable_dialogue, speech-based hooks). Owner will do this manually before Phase 3.
- What's the kkthedon cinematic color signature? Define before building cinematic_longform render path.
- What's the caption style for shortform? Font, position, color, animation.
- What's the intro/outro pattern for standard_longform stream uploads?
- Which TwelveLabs queries actually work best for music process content? (Tune in week 1 of cinematic mode.)
- What's the threshold for "moment worth keeping" in each mode? (Tune on real footage.)
- **(v2 enhancement, not now)** MLX-Whisper as an optional fast-path for bulk long-form transcription where word-timing precision isn't critical. Runs on the M1 GPU (much faster than CPU faster-whisper) but has worse word timestamps, so it's unsuitable for shortform clip cutting. Evaluate only if long-form transcription volume becomes a throughput bottleneck.

Resolve these as you build. Move them out of Open Questions into the relevant section above when answered.

---

## Phase 3.5 — Pre-Phase 4 Tuning

*(Near-term scoring-quality fixes that must land so clip selection is meaningful before Phase 4 rendering.)*

- **✅ COMPLETE (2026-05-27) — Window aggregation rework (mean → 90th percentile).** Was: per-window MEAN compressed composites to a ~0.25–0.36 ceiling (MrBeast best 0.361, all 542 windows 0.25–0.36, no spread). Now: `slide_windows` aggregates each signal's window slice via `aggregate_window(method="percentile", percentile=90)` (config `scoring.window_aggregation`/`window_percentile`, mode-overridable; shortform explicit). Result on MrBeast: best window **0.744**, spread **0.46–0.74**, **10 moments clear 0.55** (was 0). `score_threshold` held at 0.55 (re-evaluated — now meaningful; revisit on calmer content). Details under Decisions Made → Intelligence + scoring (Phase 3.5). Engine scoring is now ready for Phase 4 rendering.

---

## Phase 5 — DEFERRED (scope captured for resume) — 2026-05-29 (Session 11)

**Status: NOT abandoned, postponed.** Phase 5 (OpenCLIP + TwelveLabs + composite reweighting) was kicked off and **fully scoped** (Checkpoint 1 approved), then deferred before any code was written. This section captures the entire approved plan so a future session resumes from CP2 (test cases) without re-planning. Do NOT re-run discovery or re-decide the questions below — they are answered.

### Why deferred (honest reasons, 2026-05-29)
1. **Hard deadline:** content must ship by **June 3rd, 2026** (3 days out). Building Phase 5 tonight risked missing content delivery for engine completeness.
2. **Phase 4.5 already produces usable shortform clips** — the engine meets tonight's content needs without OpenCLIP/TwelveLabs.
3. **Render iteration speed is the bigger bottleneck** — 20+ min per render with the ~5h mid-pipeline stall risk makes Phase 5 validation impractical. **Phase 6 (performance/resume/cache) must come first.**
4. **8GB RAM risk** — sequential model loading is the right design but adds complexity that couldn't be validated properly tonight.
5. Session fatigue — better to defer cleanly than ship Phase 5 half-validated.

### Approved CP1 strategy (resume from here)
- **Order: A → B → C, sequential.**
  - **A. OpenCLIP first** — local, free, no network/quota/cost risk, validatable offline; activates the fully-specified stubbed `openclip_aesthetic` signal. Lowest risk.
  - **B. TwelveLabs second** — API-bound, has setup/cost/failure surface; isolating it after A means an A-only fallback ships even if TwelveLabs stalls.
  - **C. Reweighting last** — depends on both signals existing; propose starting weights, validate direction on the short clip, fine-tune via tuning log (anti-pattern: don't tune before real output).
  - *Runtime note:* "TwelveLabs upload kicks off FIRST" (arch rule 6) is a **runtime** ordering (launch slow upload, run local analysis while it indexes), NOT a build ordering. Build B after A.
- **OpenCLIP model:** **ViT-B-32 / openai** (config default). Sequential **load-and-release** lifecycle — load inside `analyze_openclip`, `del` + `torch.mps.empty_cache()` on exit so CLIP never coexists with WhisperX/YOLO in memory. MPS with CPU fallback (mirrors wav2vec2/YOLO).
- **Sampling rate:** keep config default **0.5 Hz** (1 frame / 2s) — aesthetic is slowly-varying; minimal overhead.
- **Output schema:** **separate `analysis/openclip.json`** (per SPEC §1), NOT folded into aesthetics.json. CLIP `openclip_aesthetic` **supplements** (does not replace) cv2 `lighting_quality`/`visual_composition` — they are distinct weight keys in the cinematic profile and coexist by design.
- **Activation path:** produce a per-window CLIP aesthetic timeline → surface via `SignalData` (new `cinematic_times`/`cinematic_score` fields) → `build_cinematic_signals` emits `openclip_aesthetic` → redistribution automatically stops for it.
- **TwelveLabs semantic mapping:** derive a scalar **`semantic_relevance`** timeline (from query-match confidence) for the composite; store **raw tags per moment** in moments.json as metadata.
- **Re-run caching (minimum Phase-5 overhead):** write both artifacts to `analysis/*.json`; add a **skip-if-exists guard keyed by source content hash** for the expensive **TwelveLabs** stage (avoids re-indexing/re-billing). Full resume is Phase 6.

### Blocker found + decision
- **`TWELVELABS_INDEX_ID` is MISSING from `.env`** (only `TWELVELABS_API_KEY` present), but `config.yaml` and `config.py` (`TwelveLabsAPIConfig.index_id_env`) both expect it. TwelveLabs cannot index without an index.
- **Decision = Option B:** the engine **creates an index on first run and persists the ID to a workspace state file** (rather than requiring a manually-provisioned id in `.env`). Implement this as part of Phase 5 B.

### Q1–Q5 answers (locked — do not re-decide on resume)
- **Q1 (index_id):** Option B — engine creates index on first run, persists ID to a workspace state file.
- **Q2 (model):** **ViT-B-32 locked** (8GB RAM constraint). Do not use ViT-L-14.
- **Q3 (CLIP prompts):** **Hardcode as module constants** in `openclip.py` (like subjects.py constants) — no config-schema change.
- **Q4 (unmapped tags):** Tags that don't fit a signal category become **descriptive metadata in moments.json** — not forced into the composite.
- **Q5 (starting shortform weights):** `cinematic_score 0.05`, `semantic_relevance 0.10`, the existing six rebalanced down to fill the remaining 0.85. Starting values only — validate direction on the short clip, then tune.

### File scope (already planned)
- **A — OpenCLIP:** NEW `src/analysis/openclip.py`; EDIT `src/analysis/__init__.py` (stage + `AnalysisOutputs.openclip_path`), `src/intelligence/scoring.py` (`SignalData` fields), `src/intelligence/__init__.py` (`_load_signal_data` reads openclip.json), `src/intelligence/cinematic_score.py` (emit `openclip_aesthetic`); TESTS `tests/test_analysis.py`, `tests/test_scoring.py`.
- **B — TwelveLabs:** NEW `src/analysis/twelvelabs.py` (+ hash-cache + index-bootstrap state file); EDIT `src/analysis/__init__.py` (stage, runtime upload-first; `--no-twelvelabs` already wired in cli.py), `src/intelligence/scoring.py` + `__init__.py` (semantic plumbing), `src/intelligence/ranking.py` (moments.json `semantic_tags` per moment); TESTS as above.
- **C — Reweighting:** EDIT `content_modes/shortform.yaml`, `cinematic_longform.yaml`, `standard_longform.yaml` (scoring_weights); `knowledge.md` tuning log; `readme.md`.
- **Frozen/never-touch:** all `src/render/*`; the four never-touch files (claude.md, POST_PRODUCTION_INTELLIGENCE_SPEC.md, dont_read_this_yet.md, message.md).

### Resume triggers
Re-open Phase 5 (resume at **CP2 — test cases**, the CP1 strategy above is approved) when **BOTH**:
1. **Phase 6 has shipped** (resume capability + cached-workspace detection + perf — the 6h iteration cycle must come down first), **AND**
2. **Game 1 content is published** (post-June-3rd, 2026 — deadline pressure cleared).

---

## Phase 10 — Professional Color Pipeline (SHIPPED 2026-06-03)

### Background
Current cinematic_longform applies LUT only (creative grade stage). Industry-standard color workflow has two stages that should happen in the engine BEFORE any Descript polish:

**Stage 1: Technical correction (currently missing)**
- Exposure normalize
- White balance fix
- Contrast standardization

**Stage 2: Creative grade (currently partial — LUT only)**
- LUT (Sony Cool 709 — current Phase 8 implementation)
- Custom curves (not yet implemented)
- Saturation shaping (not yet implemented)

### Order in cinematic pipeline (BEFORE Descript polish)
1. Engine: technical correction (exposure normalize, WB fix, contrast standardize)
2. Engine: creative grade (LUT applied to corrected input)
3. Engine: render to output format
4. Descript: polish (color adjustments as final tweaks, not foundational fixes)

### Critical ordering rule
Technical correction MUST happen before LUT because LUTs assume normalized input. Sony Cool 709 LUT expects properly-exposed S-Log3 footage to map correctly to Rec.709. Bad exposure or wrong WB feeding into the LUT produces unpredictable results.

Descript color adjustments come AFTER all engine color work because Descript polish is for refinement, not foundational correction.

### Per-pipeline enablement (when Phase 10 is built)
- **cinematic_longform**: technical correction REQUIRED (LUT expects it)
- **standard_longform**: technical correction OPTIONAL (Descript handles color)
- **shortform**: not applicable (inherits color from longform via reprocess)

### Future YAML structure when Phase 10 ships

```yaml
# cinematic_longform.yaml
color_pipeline:
  technical_correction:
    enabled: true  # required because LUT expects normalized input
  creative_grade:
    lut: cinematic_default.cube
```

```yaml
# standard_longform.yaml
color_pipeline:
  technical_correction:
    enabled: false  # default — Descript handles via polish
  # no creative_grade block (no LUT for standard_longform)
```

### Decision criterion for building Phase 10
Revisit after producing 3-5 real cinematic pieces with current Phase 8 pipeline. Check output for:
- Color consistency across shots from different conditions
- Skin tone consistency
- Overall "professional grade" feel vs "filter applied" feel

If output is inconsistent across shots with varied baseline exposure/WB:
→ Build Phase 10 (estimated 15-25 hours engineering)

If output is consistent enough with current LUT-only approach:
→ Skip Phase 10 entirely, current pipeline is sufficient

### Implementation sketch (for future reference)
- New module: `src/render/color_pipeline.py`
- Functions: `analyze_exposure()`, `analyze_white_balance()`, `analyze_contrast()`, `build_correction_filter()`, `build_creative_filter()`
- Render path modification: prepend correction filters to existing `lut3d` chain in `build_longform_command`
- Per-mode config via `color_pipeline` block in mode YAMLs
- Reuse Phase 8 LUT infrastructure unchanged
- Estimated 15-25 hours focused engineering

### Implementation notes (as shipped — 2026-06-03)

Built via the CP1→CP3 checkpointed TDD contract (43 RED-first → 6-batch diff review). Scope held to **Stage-1 technical correction only**; custom curves + saturation shaping stay deferred to a potential Phase 11.

**Module:** `src/render/color_pipeline.py` — window-scoped `analyze_exposure`/`analyze_white_balance`/`analyze_contrast` + pure helpers (`window_sample_times`, `exposure_adjustment`, `gray_world_gains`, `contrast_curve_points`, `kelvin_to_gains`) + `build_correction_filter` + the `get_correction_for_mode` entry point. Config: `ColorPipelineConfig` / `TechnicalCorrectionSettings` / `Exposure|WhiteBalance|ContrastSettings` (`config.py`, all `extra="forbid"`, range-validated). Per-mode `color_pipeline:` block — cinematic enabled, standard explicit-disabled, shortform N/A.

**Window-scoped, not whole-source.** Corrections are tuned to the SELECTED cut window `[best.start_sec, end]` (absolute source timestamps), not the whole file — analysing footage outside the cut adds noise from content that never ships. Analyzers take `(video_path, window_start_sec, window_end_sec, sample_frames=30)`; 30 frames evenly distributed within the window.

**Filter order (critical):** `eq,colorbalance,curves` prepended BEFORE `lut3d`, then `scale` → `-vf` = `correction,lut3d,scale`. Correction must precede the LUT (Sony Cool 709 assumes normalized input). Empty correction → byte-identical to Phase 8 (the 6 frozen `build_longform_command` tests untouched).

**WB midtone-band deviation (deliberate).** White balance maps gray-world gains to ffmpeg `colorbalance` **midtone** shifts (`rm/gm/bm`), NOT shadows (`rs/gs/bs`). Most subject content — skin tones especially — lives in midtones, the correct band for global WB. Documented in `build_correction_filter`'s docstring.

**Two bugs caught by TDD (RED at integration, fixed before GREEN):**
1. `kelvin_to_gains` returned `red_gain == 1.0` for every warm source — Tanner-Helland pins red at 255 for temps ≤6600K, so a raw `ref/src` ratio carried no warmth. Fix: **sum-normalize** both blackbody triples before the ratio (5600K still maps to unity).
2. `_probe_duration` returned `frame_count/fps`, letting the final window sample land past the last decodable frame → fail-loud on a VALID short clip. Fix: `(frame_count-1)/fps`.

**Auto-detect with override.** Auto by default (gray-world WB, mid-gray 0.5 exposure, percentile contrast). Override per component: `exposure.override_value` ∈ [-1,1], `white_balance.override_kelvin` ∈ [2000,10000] (neutralizes an assumed source temp toward ~5600K), `contrast.override_curve` ∈ {soft,medium,high}. Override skips sampling for that component; validators fail loud at config load.

**Caching (Phase-6 reuse).** Computed filter cached at `workspace/<run>/analysis/color_correction.json` (`{"filter": "<string>"}`), fingerprint under manifest key `color_correction`. Key = `artifact_fingerprint(source, {color_pipeline.model_dump(), window})` = source identity (path|size|mtime) + sorted-JSON config hash + rounded window. Same source+window+config → hit; changed window/config → miss; disabled → no artifact.

**Fail-loud.** Unopenable/unsamplable source raises (no silent skip). Consequence: the frozen cinematic render test switched from a 1-byte fake source to the real 60s fixture with a `[5,45]` window — the single owner-authorized existing-test edit; all 329 baseline tests otherwise unchanged.

**Verified:** 371 passed / 2 skipped (329 baseline + 42 new), mypy (48 files) + ruff + format clean. Live cache check on `sample_60s_1080p`: cinematic wrote `eq=brightness=0.013,colorbalance=rm=…,curves=all='…'` and reused it on re-render; standard wrote nothing.

---

## Future Enhancements (Post-v1)

*(Deliberately deferred features. NOT in scope for v1. This section accumulates as features are explicitly punted — moving something here is a decision to NOT build it now.)*

- **`shortform_reach` variant for 15–35s reach-optimized clips** — a separate content mode for reach/discovery-optimized short clips, distinct from the monetization-locked 60s `shortform` mode. Not in v1. Add as a new `content_modes/*.yaml` + scoring profile if/when reach optimization becomes a goal; do not retrofit variable length into the 60s-locked `shortform` mode.
- **`viral_hooks` phrase-list expansion for hyperbolic creator styles** — the current `hooks.hook_phrases` (config.yaml) are generic ("yo", "wait", "no way", …). MrBeast-style content leans on hyperbolic phrases ("biggest ever", "insane", "you won't believe", "$1,000,000", "last to leave", …). Expanding the list would raise the `viral_hooks` signal on that content style. NOT a Phase 3.5 item (3.5 is aggregation math only) — pure future tuning. (Observed on MrBeast.mp4: only 25 hooks over 10 min despite hyperbole-heavy narration.)
- **Per-content-mode hook lists** — hook phrases are currently global in `config.yaml`. Long-term it may help to make `hook_phrases` mode-specific (gameplay vs music-process vs vlog use different attention grabbers), e.g. move the list into `content_modes/*.yaml`. Future tuning, not Phase 3.5.
- **Per-word karaoke caption highlight (shortform)** — Phase 4 v1 burns grouped pop-on caption lines (≤`max_words_per_line` words, timed from word start/end). A later refinement: true word-by-word karaoke highlight (ASS `\k` tags or per-word colour fills) so the active word lights up — the high-retention TikTok caption look. Deferred; the ASS generator structure makes it a localized change.
- **Face-aware 9:16 reframe** — Phase 4 reframe is `center_crop` (default) | `blur_pad`, both content-agnostic. A smart crop that tracks the speaker's face/subject would avoid cropping off off-center subjects (esp. gaming UI / talking-head). Requires face detection (not built; OpenCLIP/face model lands Phase 5+). Deferred; slots in as a new `reframe_method` value. Activates the INERT `composition.crop_focal_y` / `face_fill_target` config (stored since Phase 4).
- **Robust multi-person re-ID for `track` (Phase 4.5 Layer 2 deferral)** — the `track` reframe currently follows the GROUP-bbox x-center (union of all person boxes). Correct for solo (one box), but in a window with transient extra people the center jumps to the group midpoint rather than staying locked on the main subject. The solo-but-wide guard (`max_spread>0.5 → blur_pad`) keeps this from firing on genuinely busy windows, so it's safe for v1. A proper fix = per-person tracking IDs (ByteTrack/BoT-SORT, built into ultralytics) + a "main subject" heuristic (largest/most-central/most-persistent track) so `track` follows ONE person across a crowd. Deferred; would replace the group-bbox center in `plan_clip`'s `centers` build.
- **v1.5 — face-landmark anchoring for zoom_crop (Phase 4.5 Session 10 deferral)** — `zoom_crop_x_for_window` currently feeds the median of YOLO **person-bbox** centres into `compute_zoom_crop_x`. Person-bbox centres drift when bodies are large or partially occluded (the bbox covers torso/arms, not the face), which produced the 2/10 GROW residuals where the centring math was right but the input x was off the speaker's face. Fix: swap the subject-x source for a face-centre x (MediaPipe Face Landmarker, YOLO-Face, or whichever ships cleanest in our torch/MPS stack). **The centring math itself does not change** — `compute_zoom_crop_x` stays exactly as is; only `zoom_crop_x_for_window` swaps its centre source. Deferred to v1.5 (not Phase 5); revisit after Phase 5 lands OpenCLIP/TwelveLabs.
- **Shortform motion + composition (deferred, kkthedon-listed Session 5):** slow push-in / micro-drift motion (`effects.py` — Phase 4 polish or v2); split facecam+gameplay composition (needs two video sources + a layout compositor — v2); beat-synced motion (Phase 5 cinematic); facial sharpness enhancement (generative, v2+); symbolic overlays / motifs (brand assets + creative direction). NOT built now — `composition:` zones in shortform.yaml are reserved where relevant; per-word karaoke captions and true blurred-bg `blur_pad` (above) are the nearest-term polish.

---

## Anti-Patterns to Avoid

*(Lessons learned the hard way. Add as you hit them.)*

- **Don't tune weights before seeing real output.** Always run on real footage first, then tune.
- **Don't add a fourth content mode "to be safe."** Three is enough. Add a fourth when there's a clear use case the existing three can't handle.
- **Don't build cinematic mode before the aesthetic is defined.** Without LUTs, fonts, and transition language, cinematic mode is just slow standard mode.
- **Don't run multiple Claude Code sessions on the same codebase simultaneously.** Creates merge conflicts and architectural drift.
- **Don't optimize before it works.** Get end-to-end functioning first, then optimize specific bottlenecks identified by real measurement.
- **Don't dynamically lower scoring thresholds to force results.** An empty `moments.json` is honest signal that the source is insufficient or scoring weights need tuning. Better to fail honestly than to silently lower quality bars. (Phase 3: the 30s fixture may legitimately produce zero moments under `score_threshold: 0.55` — report the raw score, do not relax the gate.)

---

## Last Updated

*(Update this date whenever you edit this file.)*

**Date:** 2026-06-03
**By:** Phase 10 (Session 16) — Professional Color Pipeline (Stage-1 technical correction)
**Changes:** Shipped Phase 10 via the CP1→CP3 checkpointed TDD contract (43 RED-first → 6-batch diff review). New `src/render/color_pipeline.py` (window-scoped auto exposure/WB/contrast → `eq,colorbalance,curves`, prepended BEFORE `lut3d`) + `ColorPipelineConfig` schema (`config.py`) + per-mode `color_pipeline:` blocks (cinematic enabled / standard disabled / shortform N/A) + filter-chain & Phase-6 cache wiring in `longform.py`. Auto-detect with per-component config override; fail-loud on bad source; WB uses the colorbalance MIDTONE band. TDD caught two bugs pre-GREEN: kelvin red-saturation (sum-normalize) and a frame-sampling EOF off-by-one (`_probe_duration` → `(frame_count-1)/fps`). One owner-authorized frozen-test edit (cinematic render test → real fixture, `[5,45]`); 329 baseline otherwise unchanged. 371 passed / 2 skipped, mypy + ruff + format clean. Phase 10 CLOSED; custom curves + saturation shaping deferred to potential Phase 11. Commit: "Phase 10: Professional Color Pipeline …".

---

**Date:** 2026-06-02
**By:** Phase 9 (Session 15) — Descript handoff automation (upload → one combined Underlord prompt → publish → download), longform-only
**Changes:** Shipped Phase 9 via the CP1→CP3 checkpointed TDD contract. New `src/descript/{auth,handoff}.py` + `DescriptSettings` (config.py) + `--polish` on `process`/`reprocess` + per-mode `descript:` blocks. standard/cinematic hand their final cut to Descript for ONE combined Underlord prompt (Eye Contact + Studio Sound + Color Adjustments; standard adds captions; cinematic imports a user-provided music bed). **Live-verified:** standard_longform PASS (14 credits; captions+effects applied; Color Adjustments CONFIRMED Underlord-accessible); fixed a live-caught bug — import needs `add_compositions` to place the clip on the timeline, else publish fails on an empty composition; baked in `ai_credits_used` logging. **Late architecture correction:** shortform `--polish` REMOVED (shortform is cut FROM polished longform → redundant) — now errors with guidance; removed `--polish-clips`, `default_clip_selection`, and clip-selection logic. 48 Descript tests (was 56), 282 baseline unchanged, no frozen render code touched. cinematic live test deferred until `src/branding/music/cinematic_bed.mp3` is provided.

**Date:** 2026-05-31
**By:** Phase 8 (Session 14) — cinematic_longform pipeline (single-stage LUT baseline for Descript)
**Changes:** Shipped Phase 8 via the CP1→CP3 checkpointed TDD contract (21 tests RED-first → 2-batch diff review). cinematic_longform is the standard_longform Descript-prep path **plus one thing**: a single-stage `lut3d` colour grade. Net new logic is tiny — `LutGrade` model + `LongformClipSpec.lut` optional field + `resolve_lut_grade` helper + a `lut3d` prepend in `build_longform_command`, all in `src/render/longform.py`; `cli.py` routes `cinematic_longform` into the same `run_longform_render` (LUT read internally). No new module, no scoring change, no shortform/standard regression.
- **Single-stage LUT decision.** The shipped `cinematic_default.cube` is Sony's "Cool 709" — S-Log3/S-Gamut3.Cine → graded Rec.709 in ONE step (`LUT_3D_SIZE 33`). Phase 8 applies it at full intensity via `-vf "lut3d=<path>:interp=tetrahedral,scale=W:H"` — LUT BEFORE scale so the grade lands at source resolution, then downsamples. Explicitly NOT built: two-stage LUT chains, intensity blend, multiple selectable creative LUTs (swap by replacing the one file). The pack expects S-Log3 input; Problem.mp4 isn't log so the verification grade looks "unusual" — the point was that the chain runs (proven: mean-RGB delta 27.5 graded vs 2.4 encode-noise for standard).
- **Cinematic scoring = Option X (unchanged from Phase 3).** `score_cinematic` routes via `_SCORERS["cinematic_longform"]`; signals `emotional_moments`/`lighting_quality`/`visual_composition`/`pacing_curve` available, `openclip_aesthetic`+`soundtrack_sync` unavailable → redistributed (configured 0.25 emotional renormalizes to 0.25/0.80). Verified live on Problem.mp4 (best window 0.592 > 0.400). Phase 5 (OpenCLIP) later lights up `openclip_aesthetic`.
- **LUT commit exception (owner-authorized, Path A).** `cinematic_default.cube` is force-added past `.gitignore:26` (`src/branding/luts/` stays ignored for all OTHER brand assets) because Sony NewLook is freely distributed for creator use and kkthedon wants a self-contained repo. The general `claude.md:154` "don't commit LUTs" rule is **left as written** — this is a documented single-file exception, not a policy rewrite. `resolve_lut_grade` does a **runtime** existence check (render entry, not config load) and fails loudly with the resolved absolute path if the LUT is ever missing — so a deleted LUT is obvious, not silent.
- **Scope-locked baseline.** OUT of Phase 8 (future Phase 9+ / separate Documentary Engine / Descript): multi-source assembly, transitions/dissolves, music-synced cuts, 4K, 24fps, H.265, branding intro/outro, OpenCLIP/TwelveLabs. Phase 8 = hard-cut single best window, 1080p, source fps, H.264, LUT — nothing more.
- **Verified:** 280 passed / 2 skipped (259 baseline + 21 new), mypy (45 files) + ruff + format clean, zero pre-existing tests modified beyond the prep `test_scoring.py:447` Option-X assertion. Post-build on Problem.mp4 (`--transcribe --no-twelvelabs`): cinematic → 1080p 16:9 H.264 254s graded cut at `src/output/longform/cinematic/`, 3 thumbnail JPGs, moments.json (`status: ok`, 3 candidates, 3 text); analysis was a full Phase-6 cache-hit (same source → same cache across modes). Regressions: standard_longform ungraded (delta 2.4), shortform 8 dual-output clips — both intact. **Phase 8 CLOSED — all three promised pipelines (shortform + standard_longform + cinematic_longform) now ship.** Commit: "Phase 8: cinematic_longform pipeline — single-stage LUT baseline for Descript-prep".

---

**Date:** 2026-05-30
**By:** Phase 7 (Session 13) — standard_longform Descript-prep render path + thumbnail prep
**Changes:** Shipped Phase 7 via the CP1→CP3 + 5-batch checkpointed TDD contract. NEW `src/render/longform.py` (isolated 16:9 best-window cut — plain `-vf scale`, no reframe/captions/overlays; videotoolbox→libx264 fallback duplicated, shares zero code with shortform) and `src/intelligence/thumbnail_text.py` (3–7 word quotable phrases, mean-confidence ranking, 4-value status). Filled the `render/thumbnails.py` stub (aesthetics composite = composition+lighting+sharpness; `color_harmony` deliberately excluded; min-gap distinctness; middle-frame fallback). `MomentsResult` gains 3 additive optional fields (`thumbnail_candidates`/`thumbnail_text_suggestions`/`thumbnail_text_status`, default empty/"not_generated") — schema-consistency over byte-equivalence; backward-compat guard added. `cli.py` routes standard_longform→longform (shortform unchanged, cinematic→Phase-8 notice). `standard_longform.yaml` D4 edits (preferred_duration_sec 300, base_dir → src/output/longform/standard, thumbnail_count 5→3, +thumbnail_text_count 3) — no config.py schema change. **Verified:** 259 passed / 2 skipped (227 baseline + 32 new), mypy + ruff + format clean, zero pre-existing tests modified. Post-commit: Problem.mp4 standard_longform → 1080p 16:9 254s cut + 3 thumbnail JPGs + moments.json (status ok, 3 candidates, 3 text); shortform regression on same source intact (dual auto/+zoom_crop/, A/B invariant, thumbnail fields at defaults, analysis cache-hit). Window scoring needed NO code change (preferred_duration_sec rides the existing scorer). Phase 7 CLOSED; Phase 8 (cinematic_longform) inherits the Descript-prep architecture. Commit: "Phase 7: standard_longform pipeline — best-window cut + thumbnail prep for Descript".

---

**Date:** 2026-05-30
**By:** Phase 6 (Session 12) — analysis caching + parallel scheduler + workspace commands + caffeinate + Rich progress
**Changes:** Shipped Phase 6 via a 5-batch checkpointed TDD pass. New `utils/parallel.py` (threads-only dependency-aware lane scheduler — CP1.5 decision, dylib-clash repro-backed) and `utils/keepawake.py` (caffeinate guard). `ingestion/workspace.py` gains fast composite fingerprints + atomic cache manifest + source-path discovery + `parse_age`/`clean_workspaces`; `ingest` is cache-aware (reuse-or-create, loud logging). `run_analysis` does per-artifact cache-skip (`from_cache`) + `fresh=` + LIGHT/HEAVY lane scheduling (heavy semaphore=1 on 8GB); `run_render` parallelizes the clip loop (RENDER lane=2, render_clip untouched, spec-ordered results). New CLI: `reprocess`, `process --fresh`, `workspace list/info/clean --dry-run`, keep_awake wrap, Rich stage/clip progress. Two new config fields (`max_heavy_workers`, `max_render_workers`). **Measured:** 46× analysis-layer on cache hit, 1.8× parallel fresh, 2.37× end-to-end on a 30s fixture (render-bound ceiling; 10–20×+ expected on long-form). Cache invariant verified (identical moments.json fresh vs cached). 227 passed / 2 skipped, mypy + ruff + format clean. A `workspace list` Rich-markup display bug was caught in live verification and fixed. Phase 5 deferral resume **trigger 1 (Phase 6 shipped) is now satisfied** — only trigger 2 (Game 1 content published, post-June-3rd) remains. Commit: "Phase 6: analysis caching (46x) + parallel scheduler + workspace commands + caffeinate guard + Rich progress". Phase 6 CLOSED; Phase 7 (standard_longform) next.

---

**Date:** 2026-05-29
**By:** Phase 5 deferral (Session 11) — scope captured for resume, Phase 6 prioritized
**Changes:** Phase 5 (OpenCLIP + TwelveLabs + composite reweighting) was kicked off and fully scoped (CP1 approved) then DEFERRED before any code — June 3rd content deadline + Phase 6 (perf) priority + 8GB risk + fatigue. New "Phase 5 — DEFERRED (scope captured for resume)" section records the full approved CP1 strategy (A→B→C order, OpenCLIP-first, ViT-B-32, sequential load/release memory lifecycle, separate analysis/openclip.json supplementing cv2 aesthetics, 0.5Hz sampling, semantic_relevance scalar + raw-tags metadata, TwelveLabs hash-cache guard), the TWELVELABS_INDEX_ID-missing blocker + decision (Option B: engine creates index on first run, persists ID to workspace state file), Q1–Q5 locked answers, full A/B/C file scope, and resume triggers (Phase 6 shipped AND Game 1 content published, post-June-3rd). No code or test changes. Docs only. Resume at CP2 (test cases) — do NOT re-plan. Commit: "Phase 5 deferred: scope captured for resume, Phase 6 prioritized".

---

**Date:** 2026-05-29
**By:** Phase 4.5 close (Session 10) — zoom_crop subject-aware centring + closeout
**Changes:** zoom_crop now centres on the subject's median group-bbox x instead of the geometric centre. New `compute_zoom_crop_x` (pure cover-scale math) in social_formats; new `zoom_crop_x_for_window` (per-window median) in render/__init__; optional `ClipRenderSpec.zoom_crop_x` (None → byte-identical geometric-centre fallback, preserving auto/blur_pad/track output). Plumbed via a `resolve_zoom_crop_x` resolver pattern in `build_clip_specs` — the auto/ folder passes nothing, the zoom_crop/ folder passes the per-moment resolver. claude.md: new Never-Touch Files section (the 4 files). Verified: **193 passed / 2 skipped** (185 baseline + 8 new tests including a numeric bug-demo that fails RED with AssertionError "subject at 1649px off-frame" on f437dd8); mypy 41 files + ruff + ruff-format clean. Visual: MrBeast + problem (Sony FX30 solo) + GROW 8/10 centred (2 residual offsets are person-bbox-vs-face issue, deferred to v1.5 face-landmark anchoring — math unchanged, only the subject-x source changes). auto/ regression check passed across all three. Phase 4.5 closed; Phase 5 (OpenCLIP + TwelveLabs) opens next session. Phase 6 promoted to immediate post-Phase-5 priority (6h render iteration cycle is blocking workflow validation). Commits: a9d5d18 "Phase 4.5: zoom_crop subject-aware centering" + (pending) docs-only follow-up.

---

**Date:** 2026-05-29
**By:** Phase 3.5 (Session 4) — window aggregation mean → 90th percentile
**Changes:** `scoring.aggregate_window(method, percentile)`; `slide_windows` aggregates per-signal window slices via it (default mean for test back-compat, production percentile via `config.yaml scoring.window_aggregation`/`window_percentile`, mode-overridable; shortform explicit). `PacingPlan` + `build_pacing_plan` carry the aggregation; `ScoringConfig` gains the 2 fields (approved schema change). Renamed `signal_means` → `signal_values` (WindowCandidate, BestWindow). Verified on MrBeast.mp4: best window 0.361 → **0.744**, spread 0.11 → 0.28, 0 → 10 moments ≥0.55; threshold held at 0.55 (now meaningful). Phase 3.5 marked complete; scoring ready for Phase 4. 138 passed / 1 skipped, mypy + ruff clean. Commit: "Phase 3.5: window aggregation mean → 90th percentile; signal_means renamed to signal_values".

---

**Date:** 2026-05-27
**By:** Phase 3 session 4 (post-implementation patch) — alignment fallback + diagnostic visibility + fallback
**Changes:** (1) MPS→CPU wav2vec2 alignment fallback (`whisper.py::align_transcript`, `CME_ALIGNMENT_DEVICE` override) for the "Output channels > 65536" MPS conv limit — reproduced + verified, recovers on CPU; note it's segment-length (not clip-length) dependent (MrBeast's 323 short segments align fine on MPS). (2) `moments.json` now always reports `best_window` (per-signal `signal_contributions` + raw `signal_means`), replacing the scalar `best_window_score`. (3) Top-N fallback (`top_n_fallback`, default 5): when nothing clears `score_threshold`, return top candidates as `below_threshold_best_candidates` + `used_fallback`, threshold never lowered. (4) Threshold held at 0.55 (Fix A skipped — 0.40 still above the ~0.36 mean-aggregation ceiling). (5) New Phase 3.5 section: window-aggregation rework (mean→percentile) required before Phase 4. Diagnostic on MrBeast.mp4 recorded in the shortform tuning log. Commits: 80ea0df (alignment), + diagnostic/fallback commit. 129 passed / 1 skipped, mypy + ruff clean.

---

**Date:** 2026-05-26
**By:** Phase 3 session (intelligence + scoring) — implementation complete
**Changes:** Built the intelligence layer (TDD): `scoring.py` (per-run min-max normalize, grid/resample, influence-envelope + coverage-mask + normalized-curve primitives, weight redistribution, composite, DTOs `WordSpan`/`Span`/`SignalData`/`ScoredTimeline`, generic `score_standard`), `emotional_score.py` (|ΔRMS| audio-dynamics proxy), `viral_score.py` (shortform composite + word-confidence gating), `cinematic_score.py` (composite + pacing-curve proxy + openclip/soundtrack redistribution), `pacing_engine.py` (mode pacing→`PacingPlan`), `clustering.py` (sliding-window candidates + greedy diversity), `ranking.py` (threshold/cap/scene-snap + `MomentsResult`/`moments.json` writer incl. `best_window_score`), `run_intelligence` orchestrator wired into `cme process`. 60 new tests, all RED-first. Verified: 117 passed / 1 skipped, mypy + ruff clean; smoke `cme process real_speech_sample.mp4 --mode shortform --no-twelvelabs` produced a valid (empty, score 0.254<0.55) moments.json. Earlier this session: locked shortform to 60s flat (pre-implementation commit f8e93fc) + recorded Phase-3 design decisions + Future Enhancements section.

---

**Date:** 2026-05-26
**By:** Phase 3 session (intelligence + scoring) — pre-implementation
**Changes:** Locked shortform clips to exactly 60s flat (min=max=preferred=60) for universal cross-platform monetization; recorded the decision + trade-off under Decisions Made → Intelligence + scoring (Phase 3); created the top-level Future Enhancements (Post-v1) section with the deferred `shortform_reach` 15–35s variant. (Phase 3 implementation entry to follow at session end.)

---

**Date:** 2026-05-25
**By:** Phase 2 session (analysis layer)
**Changes:** Built the analysis layer (librosa, opencv, whisper, hooks, aesthetics) + `run_analysis` orchestration wired into `cme process`, all TDD. Documented Phase-2 decisions (band-limited speech_intensity, global-z-score peaks, cv2 aesthetic placeholders, token-based hook matching, opt-in WhisperX model test) and quirks (cv2/av libavdevice dylib clash → sequential analysis for now; librosa/audioread deprecated-stdlib warnings filtered; whisperx 3.4 submodule API; benign wav2vec2 torch-version warning). Verified: 57 passed / 1 skipped, mypy + ruff clean; smoke `cme process` produced analysis JSON for standard_longform and transcript+hooks for shortform (WhisperX transcribe+align ran end-to-end).
