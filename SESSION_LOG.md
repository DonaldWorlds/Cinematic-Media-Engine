# SESSION_LOG.md — Cinematic Media Engine

Chronological log of every Claude Code session. Update at the END of every session before closing it.

**Purpose:** When you start a new session, read the most recent entry to instantly know where you left off, what's working, what's broken, and what's next.

**Read order at session start:**
1. SPEC.md (project contract)
2. CLAUDE.md (rules)
3. KNOWLEDGE.md (decisions and learnings)
4. SESSION_LOG.md (this file — most recent entry first)

---

## How to Use This File

### At session start:
- Read the most recent entry below
- Confirm git log matches what the entry says was shipped
- Note any "Blocked On" items that might need resolution before continuing

### At session end:
- Add a new entry at the TOP (most recent first)
- Fill in every section honestly — don't skip "What broke"
- Commit this file along with code changes

### Entry template (copy this for each new session):

```markdown
## Session [N] — [YYYY-MM-DD]

**Duration:** [e.g., 3 hours]
**Phase:** [Phase 1, 2, 3, 4, 5, or 6 from SPEC.md]
**Goal going in:** [What you set out to do]

### What shipped
- [Specific things completed and committed]

### What broke
- [Errors, bugs, dead ends — be honest]

### What I learned
- [Anything worth adding to KNOWLEDGE.md later]

### Blocked on
- [Anything stopping progress, if any]

### Next session starts with
- [Concrete next step — be specific]

### Commits made this session
- [hash] [commit message]
- [hash] [commit message]

### Files touched
- [List of files created or modified]
```

---

## Session Entries (most recent first)

---

## Session 16 (Phase 10 — Professional Color Pipeline) — 2026-06-03

**Phase:** Phase 10 (Stage-1 technical correction: exposure / white-balance / contrast, BEFORE the LUT).
**Goal going in:** add an auto-detect (with config override) technical-correction stage that normalizes the cut before the cinematic LUT — required for cinematic_longform, optional/off for standard_longform, N/A for shortform.

### What shipped
- `src/render/color_pipeline.py` — window-scoped `analyze_exposure`/`analyze_white_balance`/`analyze_contrast`, pure helpers, `build_correction_filter` (`eq,colorbalance,curves`), `get_correction_for_mode`. Fail-loud on bad source.
- `config.py` — `ColorPipelineConfig` + `TechnicalCorrectionSettings` + `Exposure/WhiteBalance/ContrastSettings` (all `extra="forbid"`, range-validated); `ModeConfig.color_pipeline`.
- `content_modes/cinematic_longform.yaml` (enabled) + `standard_longform.yaml` (explicit-disabled).
- `longform.py` — `LongformClipSpec.correction`, prepend BEFORE `lut3d`, `_resolve_correction` cache wrapper in `run_longform_render` (window `[best.start_sec, end]`).
- 42 new tests (`tests/test_color_pipeline.py`); 1 authorized edit to the cinematic render test.

### What broke (caught by TDD, fixed pre-GREEN)
- `kelvin_to_gains` → `red_gain == 1.0` for all warm sources (Tanner-Helland red saturation) → sum-normalize fix.
- `_probe_duration` (`frame_count/fps`) let the last sample fall past EOF → fail-loud on a valid 2s clip → `(frame_count-1)/fps`.

### What I learned
- WB belongs in the colorbalance MIDTONE band (skin tones), not shadows.
- Window-scoped sampling keeps the correction matched to what actually ships.
- Fail-loud collided with one frozen test feeding a 1-byte fake source through cinematic → one owner-authorized test edit (real fixture + `[5,45]`).

### Blocked on
- Nothing.

### Next session starts with
- Visual A/B on real varied-lighting footage (Phase 8 LUT-only vs Phase 10 correction+LUT) before production reliance.
- Optional Phase 11 (creative grade: custom curves + saturation) if output needs more shaping; else Phase 5 (OpenCLIP/TwelveLabs) is the other open track.

### Commits made this session
- [pending] Phase 10: Professional Color Pipeline — technical correction stage (auto-detect exposure/WB/contrast with config override) for cinematic_longform

### Files touched
- src/render/color_pipeline.py (stub → full), src/config.py, content_modes/{cinematic,standard}_longform.yaml, src/render/longform.py, tests/test_color_pipeline.py (new), tests/test_render.py (1 edit), KNOWLEDGE.md, SESSION_LOG.md, README.md

---

## Session 15 (Phase 9 — Descript handoff automation) — 2026-06-01

**Phase:** Phase 9 (automated Descript polish). Local build CLOSED this session; live API verification + commit pending.
**Goal going in:** engine uploads its final cut to Descript via the REST API, runs ONE combined Underlord prompt per video (Interpretation A — engine cuts, Descript polishes), publishes, downloads. All three pipelines. Sync, fail-loud, minimum-viable.

### Process
- Full checkpointed contract: PREP verification → CP1 strategy + 11 open-question answers → CP2 52 test cases → CP2.5 RED proof (collection ImportError: no `src.descript` / `DescriptSettings`) → CP3 batches **A+B** (auth+handoff+config) / **C** (3 YAMLs) / **D** (cli) / **E** (pyproject+docs), per-batch diff review. 52 tests written RED-first, mocked at the HTTP boundary only.
- **No Descript MCP tools available this session** → REST API called directly via `requests` (no Python SDK exists). Logged the standing DISCIPLINE rule (always cross-check `docs/descript_api_research.md` + active MCP tools before Descript work) into KNOWLEDGE.md.

### What shipped (pending commit)
- **`src/descript/auth.py`** (`load_descript_token`) + **`src/descript/handoff.py`** (`upload_to_descript`, `import_media`, `_resolve_composition`, `run_underlord`, `wait_for_job` [two-level success], `publish_project`, `download_polished`, `polish_video`, `dispatch_polish`, `build_polished_path`; `DescriptError`/`RateLimitError`).
- **`src/config.py`:** `DescriptSettings` + optional `descript` field on `ModeConfig`. **2 `content_modes/*.yaml`:** `descript:` blocks (standard YouTube captions; cinematic imported-music + color-on-LUT) — shortform has NONE (polish is longform-only). **`src/cli.py`:** `--polish` on `process`+`reprocess` (errors on shortform). **`pyproject.toml`:** `requests` direct dep. **Docs:** KNOWLEDGE.md Phase 9 close + corrections, this entry, README `--polish` section.

### What I learned / corrected mid-build
- **Color:** Descript has NO LUT/grading, but DOES have "Color Adjustments" (exposure/contrast/WB/presets); Underlord-prompt access was undocumented → kept in prompts and **CONFIRMED working at live test** (Underlord applied real exposure/contrast/WB values). Owner: keep Sony LUT baked, add Descript color adjustments on top. Updated `docs/descript_api_research.md` §1.1 (Studio Sound is file-level).
- **"Combined prompt cheaper" is NOT documented** — it's the recommended one-shot pattern for *correctness*, not a proven cost saving (research L1235 vs L535).
- **Music:** changed to USER-PROVIDED bed (imported + prompted), overriding the locked stock-only decision; existence checked before any HTTP.

### Live verification + late correction (2026-06-02)
- **Live-caught bug (mocks couldn't):** import with `add_media` alone left an EMPTY composition (`duration: 0`) → Underlord had nothing to edit (4 cr, agent_response "composition has no media content") and publish failed "Job failed unexpectedly". Fix: import now sends `add_compositions` to place the clip on the timeline (research §2.5/§2.11). The two-level `wait_for_job` check caught it fail-loud — handoff code was correct; diagnosis from `agent_response` + `GET /projects`.
- **Color Adjustments CONFIRMED** Underlord-accessible (CP1 open question resolved). **Per-video ≈ 14 AI credits** (Eye Contact 10 + Studio Sound + Color Adjustments), the same for a 60s clip and a 254s cut → per-effect, not duration-scaled. `polish_video` now logs `ai_credits_used`.
- **ARCHITECTURE: shortform `--polish` REMOVED** (kkthedon) — shortform is cut FROM polished longform, so polishing it again is redundant; now errors with guidance. Removed `--polish-clips`, `default_clip_selection`, clip-selection logic. **Process lesson:** at CP1, validate the END-TO-END production workflow (where each pipeline's INPUT comes from), not just per-pipeline API mechanics.

### Verification
- **328 passed / 2 skipped** (282 baseline unchanged + 48 Descript), mypy (48 files) + ruff + ruff-format clean. Live: shortform `--polish` errors cleanly (free); **standard_longform PASS** (14 cr, 247→350 MB, captions + Studio Sound + Eye Contact + Color Adjustments, ~4.5 min). No frozen render code touched.

### Blocked on
- **cinematic_longform live test** deferred until the owner drops `src/branding/music/cinematic_bed.mp3` (the run fail-loud-checks for it before any HTTP). Everything else live-verified.

### Next session starts with
- (Optional) cinematic_longform live test once the music file is in place: `cme process tests/fixtures/Problem.mp4 --mode cinematic_longform --transcribe --polish`. Otherwise Phase 9 is closed.

### Files touched
- NEW: `src/descript/__init__.py`, `src/descript/auth.py`, `src/descript/handoff.py`, `tests/test_descript.py`. EDITED: `src/config.py`, `src/cli.py`, `content_modes/{shortform,standard_longform,cinematic_longform}.yaml`, `pyproject.toml`, `uv.lock`, `KNOWLEDGE.md`, `SESSION_LOG.md`, `README.md`, `docs/descript_api_research.md`.

---

## Session 14 (Phase 8 — cinematic_longform single-stage LUT baseline) — 2026-05-31

**Phase:** Phase 8 (cinematic_longform render path). CLOSED this session. **Last of the three promised pipelines.**
**Goal going in:** ship cinematic_longform as the standard_longform Descript-prep path + a single-stage Sony LUT colour grade, routed via the existing cinematic scorer. Scope locked to baseline — no transitions/4K/24fps/multi-source (those are Phase 9+ / a separate Documentary Engine / Descript).

### Process
- Full checkpointed contract: CP1 strategy + open-question answers → CP2 test cases → CP2.5 RED proof (18 failed for the right reasons: ImportError on `LutGrade`/`resolve_lut_grade`, AttributeError on `spec.lut`, dispatch AssertionError on the "not built" notice; 3 correctly green = prep + regression guard) → CP3 2-batch diff review (longform.py whole LUT mechanism → cli.py dispatch). 21 tests written RED-first.
- **Prep verified before CP1:** new minimal cinematic_longform.yaml (top-level `color_grade` block), `*.bak` gitignore, Option-X `test_scoring.py:447` edit, real Sony LUT on disk, `load_mode_config` clean, 259-test baseline.
- **Mid-session decision (CP3 hold):** the LUT file question. CLAUDE.md:154 + `.gitignore:26` both say don't commit LUTs. Surfaced to owner with evidence; owner chose **Path A** (commit it — Sony NewLook is freely distributed). Honored with a narrow `git add -f` (rest of `src/branding/luts/` stays ignored), claude.md rule left as written, exception documented in KNOWLEDGE.md.

### What shipped (this commit)
- **`src/render/longform.py`:** `LutInterp` Literal + `LutGrade` model, `LongformClipSpec.lut` optional field, `resolve_lut_grade` (dict-read + PROJECT_ROOT resolve + runtime existence check, fail-fast loud), `build_longform_command` prepends `lut3d=<path>:interp=<interp>,` before scale only when `spec.lut` set, `run_longform_render` reads `color_grade` and passes the grade. Standard path byte-identical (lut=None).
- **`src/cli.py`:** `process` + `reprocess` route `cinematic_longform` into `run_longform_render` (tuple membership with standard_longform); stale "Phase 8 not built" notice removed.
- **`content_modes/cinematic_longform.yaml`** (prep): minimal Phase 8 version — single LUT, cinematic keys, clip_density medium, nested 1080p. **`tests/test_scoring.py`** (prep): Option-X redistribution assertion. **`.gitignore`** (prep): `*.bak`.
- **`tests/fixtures/identity.cube` (NEW):** committed identity LUT so command-string/config tests don't depend on the owner-IP Sony file.
- **`src/branding/luts/creativeStyles/cinematic_default.cube` (NEW, force-added):** the real Sony Cool 709 LUT.

### What broke / was caught
- Two ruff RUF043 warnings in new tests (`match=` strings with unescaped `.` metachar) → fixed with raw-string `match=r"color_grade\.lut_path..."`. One ruff-format reflow auto-applied. Nothing structural; CP2.5 RED was clean.

### Verification (all green)
- **280 passed / 2 skipped** (259 baseline + 21 new), mypy (45 files) + ruff + format clean, zero pre-existing tests modified beyond the prep Option-X edit.
- cinematic_longform on Problem.mp4 (`--transcribe --no-twelvelabs`): 1080p 16:9 H.264 254s **graded** cut at `src/output/longform/cinematic/`, `thumbnails/` 3 JPGs, moments.json `status: ok` + 3 candidates + 3 text; cinematic scoring (`unavailable=[openclip_aesthetic, soundtrack_sync]`, best window 0.592); analysis full Phase-6 cache-hit.
- **LUT proof:** mean-RGB delta vs plain re-encode = **27.5 (cinematic, graded)** vs **2.4 (standard, ungraded)** — the `lut3d` chain demonstrably alters pixels.
- Regressions: standard_longform ungraded 1080p cut; shortform 8 dual-output clips (auto/ + zoom_crop/) — both intact.

### Blocked on
- Nothing.

### Next session starts with
- **Phase 5 resume** (OpenCLIP + TwelveLabs — gated on Game-1 content published, post-June-3; lights up `openclip_aesthetic` for cinematic). OR **v1.5 cleanup** (strip duplicate shortform caption/watermark burn now Descript owns polish; branding REMOVAL-CANDIDATE).
- All three promised pipelines now ship — the engine is feature-complete for the Descript handoff workflow.

### Files touched
- NEW: tests/fixtures/identity.cube, src/branding/luts/creativeStyles/cinematic_default.cube (force-added)
- MOD: src/render/longform.py, src/cli.py, content_modes/cinematic_longform.yaml (prep), tests/test_scoring.py (prep), tests/test_render.py, tests/test_cli.py, .gitignore (prep), knowledge.md, SESSION_LOG.md, readme.md

---

## Session 13 (Phase 7 — standard_longform Descript-prep pipeline) — 2026-05-30

**Phase:** Phase 7 (standard_longform render path). CLOSED this session.
**Goal going in:** ship the standard_longform pipeline as an "intelligent prep tool" for Descript — single best-window 16:9 cut (no reframe/captions/overlays), thumbnail frame candidates, and quotable thumbnail-text suggestions. Engine cuts; Descript polishes.

### Process
- Full checkpointed contract: CP1 strategy → CP2 test cases → CP2.5 RED proof (28 new-behavior tests failed for the right reasons: ModuleNotFound / ImportError / AttributeError / config-key / dispatch AssertionError) → CP3 diff → 5-batch human-gated review (longform+thumbnails → ranking+thumbnail_text → yaml → cli → tests). 32 tests written RED-first.
- Key CP1 finding: window scoring needed **no code change** — `build_pacing_plan` already honors `preferred_duration_sec`; the "60s hardcode" was always shortform-only.

### What shipped (this commit)
- **`src/render/longform.py` (NEW):** `LongformClipSpec`, `build_longform_command` (plain `-vf scale`, no `-filter_complex`), `LongformBackend` (videotoolbox→libx264 fallback, duplicated not imported), `select_best_window`, `run_longform_render`. Shares ZERO code with the frozen shortform path.
- **`src/intelligence/thumbnail_text.py` (NEW):** `extract_thumbnail_text` — 3–7 word quotable phrases from `hooks.json` quotables ∩ window, mean-WhisperX-confidence ranking, 4-value status.
- **`src/render/thumbnails.py`:** filled the stub — composite frame score (composition+lighting+sharpness; color_harmony excluded), min-gap selection, middle-frame fallback, JPEG export.
- **`src/intelligence/ranking.py`:** 3 additive optional `MomentsResult` fields (defaults empty / "not_generated").
- **`src/cli.py`:** standard_longform → `run_longform_render` dispatch in `process` + `reprocess`; cinematic notice reworded to "Phase 8".
- **`content_modes/standard_longform.yaml`:** D4 — `preferred_duration_sec: 300`, `base_dir → src/output/longform/standard`, `thumbnail_count 5→3`, `+thumbnail_text_count: 3`.

### What broke / was caught
- Nothing structural. CP2.5 RED was clean. During verification my ad-hoc check indexed `moments[0]` on a fallback run (`moment_count: 0`) — script bug, not product: shortform on Problem.mp4 used the top-N fallback (calm solo clip, no window cleared 0.55), reframe diagnostics live on the fallback candidates. Re-checked correctly → green.

### Verification (all green)
- **259 passed / 2 skipped** (227 baseline + 32 new), mypy (45 files) + ruff + format clean, zero pre-existing tests modified.
- standard_longform on Problem.mp4 (`--transcribe`): 1080p 16:9 254s cut (clamped, source < 300s) at `src/output/longform/standard/`, `thumbnails/` 3 JPGs, moments.json `status: ok` + 3 candidates + 3 text suggestions.
- shortform regression on same source: auto/ + zoom_crop/ populated, identical filenames (A/B invariant), reframe present, thumbnail fields at defaults, analysis cache-hit — Phase 4.5 + 6 invariants intact.

### Blocked on
- Nothing.

### Next session starts with
- **Phase 8 — cinematic_longform** (inherits the Descript-prep architecture; adds LUT color grade + cinematic scoring). OR **Phase 5 resume** (OpenCLIP+TwelveLabs, gated on Game-1 content published).
- **v1.5 cleanup candidates:** strip shortform caption/watermark burn (now duplicate with Descript); thumbnail-text ranking tuning (length penalty / hook bonus) if real suggestions read weak; re-evaluate `color_harmony` in the thumbnail composite on real candidates.

### Files touched
- NEW: src/render/longform.py, src/intelligence/thumbnail_text.py
- MOD: src/render/thumbnails.py, src/intelligence/ranking.py, src/cli.py, content_modes/standard_longform.yaml, tests/test_render.py, tests/test_scoring.py, tests/test_cli.py, KNOWLEDGE.md, SESSION_LOG.md, README.md

---

## Session 12 (Phase 6 — caching + parallelism + workspace commands) — 2026-05-30

**Phase:** Phase 6 (performance/polish), priority-bumped from Session 10. CLOSED this session.
**Goal going in:** kill the 6h GROW iteration cycle — per-artifact analysis caching + a parallel scheduler + `reprocess`/`workspace` commands + caffeinate sleep guard + Rich progress.

### Process
- Ran the 5-batch checkpointed contract (CP1 strategy → CP1.5 parallelism design → CP2 tests → CP2.5 RED proof → CP3 diff-per-batch). 34 new tests written RED-first (32 failed / 2 guard-green against `18867c7`), then implemented batch-by-batch with per-batch approval.
- **CP1.5 dylib decision was evidence-backed:** a minimal `spawn` vs threads repro confirmed threads sidestep the cv2/av libavdevice clash AND avoid the 8GB per-worker memory blow-up → threads-only `run_graph`.

### What shipped (commit 18867c7 → this commit)
- `src/utils/parallel.py` (NEW) — dependency-aware, lane-throttled thread scheduler (deadlock-free, error-isolated).
- `src/utils/keepawake.py` (NEW) — `caffeinate -w PID` guard (macOS only), fixes the Session-10 ~5h sleep stall.
- `src/ingestion/workspace.py` — fast composite fingerprints (path+size+mtime+config, no content hash), atomic cache manifest, source-path discovery, `parse_age`, `clean_workspaces`. `ingest` cache-aware (reuse-or-create).
- `src/analysis/__init__.py` — per-artifact cache-skip (`from_cache`), `fresh=`, LIGHT/HEAVY lanes (heavy semaphore=1). hooks reloads transcript from disk (workspace-as-contract).
- `src/render/__init__.py` — parallel clip loop (RENDER lane=2); `render_clip`/filter graphs/encoder UNTOUCHED; spec-ordered results.
- `src/cli.py` — `reprocess`, `process --fresh`, `workspace list/info/clean --dry-run`, keep_awake wrap, progress.
- `config.py` + `config.yaml` — `performance.max_heavy_workers=1`, `max_render_workers=2`.

### What broke / was caught
- `workspace list` lost its status flags: `console.print` parsed the literal `[a, b]` as Rich markup. Caught in **live** verification (the CliRunner unit test parses markup differently and passed). Fixed → `(a, b)`.

### Verification (all green; speedup gate overridden)
- **227 passed / 2 skipped**, 92% coverage, zero pre-existing tests modified.
- Cache invariant: fresh vs cached `moments.json` identical except `generated_at`. Dispatch equivalence: clip sizes identical.
- Speedups on a 30s real-speech fixture: **46× analysis-layer (cache hit), 1.8× parallel fresh, 2.37× end-to-end** (render-bound ceiling for a short clip; ~91% of the 2.6× theoretical max). The 5× end-to-end gate was set on a wrong assumption (analysis-always-dominates) and **overridden by kkthedon** — 10–20×+ expected on long-form once those modes ship.

### Next session starts with
- **Phase 7 — standard_longform** (per SPEC build order), fresh session.
- Phase 5 (OpenCLIP + TwelveLabs) resume trigger 1 (Phase 6 shipped) now MET; trigger 2 (Game 1 content published, post-June-3rd) still pending.

### Files touched
- NEW: src/utils/parallel.py, src/utils/keepawake.py, tests/test_cli.py, tests/test_parallel.py
- MOD: src/analysis/__init__.py, src/cli.py, src/config.py, src/ingestion/__init__.py, src/ingestion/workspace.py, src/render/__init__.py, src/utils/progress.py, config.yaml, tests/test_analysis.py, tests/test_ingestion.py, tests/test_render.py, knowledge.md, SESSION_LOG.md

### Phase 6 close addendum (follow-up commit — cleanup)
- **Owner verification on real content (Problem.mp4, Sony FX30 solo):** fresh **5m59s**, cached **5m18s**; `workspace list` showed only the single reused run dir on the 2nd run → cache discovery + reuse confirmed end-to-end. (Small end-to-end delta because render dominates Problem.mp4; the cache win is on the analysis layer — see the 46× figure.)
- **Render slowdown on consecutive runs (~2×, e.g. 2m22s→5m15s):** attributed to ffmpeg HW-encoder fallback (h264_videotoolbox→libx264) or thermal throttling — **environmental, NOT a Phase 6 regression** (the cache/scheduler don't touch render encoding params).
- **Cleanup commit:** quieted the two per-clip "missing asset" notices (watermark, caption-font fallback) WARNING→DEBUG (the absences are expected; the "no font at all" case stays WARNING); added the **Branding architecture decision** note to KNOWLEDGE.md — branding (watermark + brand font) is deferred to **Descript** in post; the code is a **REMOVAL CANDIDATE for v1.5**, not to be extended. Commit: "Phase 6: ... + branding warning quiet + branding removal note".
- (Logged here in Session 12 rather than as a separate "Session 11" entry, since Session 11 already records the Phase 5 deferral and Session 12 is today's Phase 6 work.)

---

## Session 11 (Phase 5 kickoff attempt — DEFERRED) — 2026-05-29

**Duration:** short (planning only, no code)
**Phase:** Phase 5 (OpenCLIP + TwelveLabs + composite reweighting) — kicked off, then deferred.
**Goal going in:** open Phase 5 in full scope (OpenCLIP visual aesthetic + TwelveLabs semantic + composite reweighting), starting with docs read → summary → Checkpoint 1 strategy.

### What shipped
- **Docs-only commit.** No code. Read SPEC/CLAUDE/KNOWLEDGE/SESSION_LOG + the live scoring/analysis/render source; produced the 6-point comprehension summary and a **full Checkpoint 1 strategy (approved)**.
- **Phase 5 DEFERRED** before any implementation — captured the entire approved scope in KNOWLEDGE.md ("Phase 5 — DEFERRED (scope captured for resume)") so the next attempt resumes at CP2 (test cases) without re-planning.

### Why deferred (honest)
1. June 3rd, 2026 content deadline (3 days) — shipping content beats engine completeness tonight.
2. Phase 4.5 already produces usable shortform clips — engine meets tonight's needs.
3. Render iteration speed (20+ min/render, ~5h stall risk) makes Phase 5 validation impractical — Phase 6 (perf) must come first.
4. 8GB RAM risk + session fatigue — sequential model loading is right but needs proper validation time.

### What broke
- Nothing (no code). One real **blocker surfaced**: `TWELVELABS_INDEX_ID` missing from `.env` (only `TWELVELABS_API_KEY` present) while config expects it. Decision: Option B — engine creates the index on first run, persists ID to a workspace state file (recorded in KNOWLEDGE for resume).

### What I learned
- Phase 5 CP1 is fully scoped + Q1–Q5 answered (ViT-B-32 locked; hardcode CLIP prompts; unmapped tags → metadata; shortform starting weights cinematic_score 0.05 / semantic_relevance 0.10 / rest rebalanced). All in KNOWLEDGE.md — do not re-decide.

### Blocked on
- Nothing blocking the deferral. Phase 5 resume is GATED on: Phase 6 shipped AND Game 1 content published (post-June-3rd).

### Next session starts with
- **Phase 6 — performance** (resume capability, cached-workspace detection, the 6h iteration-cycle fix). Fresh session, fresh context, perf-focused.
- **Phase 5 resume (later):** start at **CP2 (test cases)** — the CP1 strategy in KNOWLEDGE.md is approved; do NOT re-plan.

### Commits made this session
- (this entry) "Phase 5 deferred: scope captured for resume, Phase 6 prioritized"

### Files touched
- knowledge.md (new "Phase 5 — DEFERRED" section + Last Updated entry)
- SESSION_LOG.md (this entry)

---

## Session 10 (Phase 4.5 close — zoom_crop subject-aware centering) — 2026-05-29

**Phase:** Phase 4.5 closeout. Final scoped polish before Phase 5.
**Goal going in:** the prompt opened as a track tune for solo content (max-spread → median-spread aggregation in `auto_reframe.py`). The goal was re-scoped through three iterations as the real bug came into focus: **track motion → static centering → zoom_crop-only subject-aware centering**. The track-aggregation tune was not the actual bug; the geometric-centre crop in `zoom_crop` was. Re-scoping at the boundary (Checkpoint 1) saved the session from running on the wrong premise.

### Context coming in (prior-session damage, reverted)
- A prior session had attempted the track tune but went out of scope: modified `intelligence/ranking.py` + `intelligence/__init__.py`, deleted `POST_PRODUCTION_INTELLIGENCE_SPEC.md`, and edited the never-touch `claude.md`. The whole change set was reverted via `git reset --hard f437dd8` before this session began. This session ran on a clean f437dd8 tree.

### What shipped (commit a9d5d18)
- **zoom_crop subject-aware centering.** New pure `compute_zoom_crop_x` in `social_formats` — maps subject's source-x through the cover-scale factor (`s = max(zoom_w/source_w, zoom_h/source_h)`) and clamps the crop inside the scaled image. New `zoom_crop_x_for_window` in `render/__init__` — median of group-bbox centres across the moment window. New optional `ClipRenderSpec.zoom_crop_x` (default None). `reframe_filtergraph` ZOOM_CROP branch emits `crop=W:H:x=N:y=(ih-oh)/2` when given an int, the **exact prior string** when given None. Plumbed via a `resolve_zoom_crop_x` resolver pattern in `build_clip_specs` so the auto/ folder passes nothing (zoom_crop_x stays None) and only the zoom_crop/ folder gets the per-moment resolver. `ReframePlan` (auto_reframe.py) untouched — the new field rides `ClipRenderSpec` instead.
- **`claude.md` Never-Touch Files section** (this session's explicitly authorised claude.md edit) — claude.md, POST_PRODUCTION_INTELLIGENCE_SPEC.md, dont_read_this_yet.md, message.md.
- 4 source files + tests/test_render.py + claude.md + knowledge.md. 304 insertions / 10 deletions.

### Verification
- **193 passed / 2 skipped** (= 185 baseline + 8 new). mypy 41 files + ruff check + ruff-format clean. Pre-commit ran the hook chain in full (no `--no-verify`).
- 8 RED → GREEN tests written first: 5 unit tests on `compute_zoom_crop_x` (centred / left-25 / right-75 / left-edge clamp / right-edge clamp), 1 no-detection fallback test pinning the **byte-identical** geometric-centre filtergraph string, 1 multi-person median test, 1 numeric bug-demo. The bug-demo failed against unmodified f437dd8 with a concrete AssertionError — *"75%-right subject lands at 1649px in a 1080-wide output under crop_x=1679 (geometric centre) — off-frame, the bug we are fixing"* — distinguishing real numeric brokenness from "feature absent" (the other 7 failed with ImportError).
- **Visual verification across test content (kkthedon):** MrBeast.mp4 centred; problem.mp4 (Sony FX30 solo) centred; GROW.mp4 centred on **8/10** moments. The 2 residual offsets are content-specific (multi-person group-bbox shifts and YOLO bbox variation pulling the median off the speaker's face), NOT a math bug — `compute_zoom_crop_x` is correct; the input subject-x is the imprecise quantity. auto/ and blur_pad output unchanged on all three (regression check passed). Accepted as v1 ship.

### Discipline notes (meta-lesson of the session)
- Ran a strict three-checkpoint contract: **scope → tests (+ RED proof at 2.5) → diff per file** before commit. Surfaced two scope tensions before writing any code: (1) the literal goal required editing the *frozen* `social_formats.py` (the geometric-centre crop string lives there) — flagged, asked for scope expansion approval, did not silently expand; (2) "AssertionError now AND passes after fix AND uses only existing symbols" was internally inconsistent given the deliberate None-fallback byte-preservation — resolved with a try/except shim in the bug-demo test so it surfaces as AssertionError (not ImportError) on current code while still going green after the fix. Both moves kept the contract honest instead of fudging it.
- Architectural call paid off: routing `zoom_crop_x` through `ClipRenderSpec` (not `ReframePlan`) kept the auto decision engine entirely out of the change. Auto/blur_pad/track byte-equivalence is **structural** (only the ZOOM_CROP filter branch reads the new field), not just empirical.

### Deferred (NOT in v1)
- **v1.5 — face-landmark anchoring for zoom_crop.** Replace the YOLO person-bbox median with a face-centre x (MediaPipe Face Landmarker or YOLO-Face). The centring math in `compute_zoom_crop_x` does NOT change — only `zoom_crop_x_for_window` swaps its subject-x source. Addresses the 2/10 GROW residuals where the person-bbox drifted off the speaker's face.
- **Phase 6 priority bumped.** End-to-end `cme process` on GROW (701s 4K, full WhisperX + YOLO + render of 20 clips) measured ~6h wall time this session with a multi-hour mid-pipeline stall (likely Mac sleep — `pmset` defaults idle long-running CPU). 20+ min iteration is blocking workflow validation; Phase 6 (resume capability, cached workspace detection, perf) is promoted to the immediate post-Phase-5 priority. Workaround now: `caffeinate -i` long runs, or `cme reprocess` when only intelligence/render changed.

### Blocked on
- Nothing.

### Next session starts with
- **Phase 5 — OpenCLIP + TwelveLabs (decision pending) + composite reweighting** (per SPEC build order). OpenCLIP aesthetic scoring is in. TwelveLabs is a decision point — API cost vs incremental quality on shipped content; defer if marginal. Composite reweighting: `cinematic_score` and `viral_score` absorb whichever signals come online (redistribution stops where signals are now available). Phase 6 perf work runs alongside, not after — the 6h20min iteration cycle has to come down before serious Phase-5 tuning.

### Commits made this session
- `a9d5d18` "Phase 4.5: zoom_crop subject-aware centering"
- (pending, this entry) docs-only follow-up: visual verification + v1.5 face-landmark deferral + perf note + Session 10 entry

### Files touched
- src/render/social_formats.py, src/render/backend.py, src/render/ffmpeg_pipeline.py, src/render/__init__.py
- tests/test_render.py (+189 / -0, purely additive at EOF)
- claude.md (Never-Touch Files section)
- knowledge.md (zoom_crop subject-aware bullet, visual verification, iteration-cycle / Phase 6 promotion, v1.5 face-landmark deferral, Last Updated)
- SESSION_LOG.md (this entry)

---

## Session 8 (Phase 4.5 — Layer 2: auto-reframe decision engine) — 2026-05-29

**Phase:** Phase 4.5 — content-adaptive auto-reframe. **Layer 2** (decision); Layer 1 (detection) shipped Session 7.
**Goal going in:** build `render/auto_reframe.py` — read `subjects.json` per moment window, aggregate, pick reframe method + foreground_scale via discrete buckets; `track` for sustained solo (one-euro-smoothed moving 9:16 crop); make `reframe_method: auto` the shortform default. Tests-first. Verify the MrBeast payoff (solo→track full-bleed, crowd→blur_pad+bars).

### What shipped
- **`render/auto_reframe.py`** — `aggregate_window` (MEDIAN count + MAX spread), `decide_reframe` (discrete buckets), `one_euro_smooth` (adaptive low-pass), `track_crop_path` (smoothed + clamped crop-x), `crop_x_expr` (piecewise-linear ffmpeg `x(t)`), `plan_clip` (window → `ReframePlan`). Pure + fully unit-tested.
- **`reframe_decision:` config** + `ReframeDecisionConfig` (solo_track_max_width=0.5, pair_fs=1.3, small_group_fs=1.1, small_group_max=5, large_group_fs=1.0) — all buckets tunable.
- **`track` render path** — `TrackCrop` model on `ClipRenderSpec`; `social_formats.reframe_filtergraph` track branch (moving `crop` → `scale`); threaded through `ffmpeg_pipeline`.
- **auto dispatch** — `build_clip_specs` now takes a per-moment `resolve_reframe(moment) -> ReframePlan`; `run_render` loads `subjects.json` and builds the resolver (auto = `plan_clip` per window; manual = fixed plan; missing subjects.json = blur_pad fallback + warning). **`shortform.yaml reframe_method: auto`** is the new default.

### What broke
- **ffmpeg rejected the track crop filtergraph** (`crop=w:h:x='expr':0`) → "No option name near '0'": a positional y after the named `x=` is invalid. Fix: name it (`:y=0`). Commas inside the `if(lt(...))` expr are fine — `x='...'` single-quoting protects them. Updated the (demonstrably-wrong) RED test to the `y=0` form.

### Verification
- **180 passed, 2 skipped**; mypy (41 files) + ruff + format clean.
- **MrBeast payoff (real auto path, rendered + frames extracted):** solo t=400–406s (median 1, max_spread 0.346) → **track, full-bleed, NO bars**; crowd t=280–286s (median 3, max_spread 0.943) → **blur_pad fs=1.1, full frame preserved with blurred top/bottom bars**. Different moments → different framing, confirmed visually.
- **Design note confirmed in data:** max-spread aggregation is strict — no 20s MrBeast window sustains a tight solo, so auto stays blur_pad on busy footage (correct); track fires on genuinely sustained-solo segments (kkthedon's actual use case).

### Next session starts with
- Phase 4.5 is feature-complete (detection + decision + track render). Candidate next: tune buckets on kkthedon's own solo footage; OR proceed to **Phase 5 (OpenCLIP + TwelveLabs)** per the SPEC build order. Multi-person re-ID for `track` is deferred (Future Enhancements).

### Commits
- 28ba6a1 "Phase 4.5 Layer 2: auto-reframe decision engine"

### Files touched
- src/render/auto_reframe.py (new), src/render/__init__.py, src/render/backend.py, src/render/social_formats.py, src/render/ffmpeg_pipeline.py, src/config.py, config.yaml, content_modes/shortform.yaml, tests/test_render.py, KNOWLEDGE.md, SESSION_LOG.md

---

## Session 9 (Phase 4.5 polish — bucket tune + reframe visibility + zoom_crop dual output) — 2026-05-29

**Phase:** Phase 4.5 — final polish before Phase 5. Three changes in one pass, then Phase 4.5 closes.
**Goal going in:** (1) tune blur_pad buckets DOWN (1.1/1.3 cropped edges on real MrBeast); (2) write the reframe decision into moments.json per moment for tuning visibility; (3) add a `zoom_crop` reframe + `dual_output` mode that renders every moment twice (auto/ + zoom_crop/).

### What shipped
- **Bucket tune + rename** (`config.yaml` + `config.py`): `pair_fs/small_group_fs/large_group_fs` → `bucket_2_fs(1.3→1.2)`/`bucket_3_5_fs(1.1→1.0)`/`bucket_6_plus_fs(1.0)`; `solo_track_max_width` unchanged. Rationale: fs>1.0 enlarges the sharp band and crops L/R edges; owner's known-good is fs=1.0 (keep all content, bigger bars).
- **Reframe diagnostic in moments.json**: `ReframeDecision` model (`intelligence/ranking.py`) + optional `Moment.reframe = {method, foreground_scale, median_count, max_spread}`. `run_render` resolves each moment ONCE (`_reframe_for_moment` → plan + decision), attaches the decision, and re-writes `moments.json`. Cross-layer write is deliberate/owner-requested (model lives in the lower layer so no reverse import).
- **`zoom_crop` method**: `social_formats.zoom_crop_box` + filtergraph branch (cover a `fs`-scaled box, centre-crop to target — full-bleed punch-in, no bars). Config `zoom_crop_decision.foreground_scale` (default 1.3) + `ZoomCropConfig`. NOT a decision bucket (parallel only).
- **`dual_output: true`** (shortform default): `run_render` renders auto/ + zoom_crop/ with identical filenames via a `specs_for(out_dir, resolver)` closure. 10 moments → 20 clips.

### What broke
- Nothing structural. Pre-existing single-output run_render test had to be updated for the new dual default (1 → 2 outputs; select the auto/ spec).

### Verification
- **185 passed, 2 skipped**; mypy (41 files) + ruff + format clean. New tests TDD'd RED→GREEN: zoom_crop box math, zoom_crop filtergraph (+clamp), dual_output run_render.
- **MrBeast re-render (dual):** all 10 ranked moments are blur_pad (median 1–2 over their 60s windows); 2-person clips now fs=**1.2**, solo-but-wide fs=1.0. Direct fs=1.0 vs old fs=1.1 frame comparison on the t=280 crowd window: 1.1 clips ~54px off each side (left timer box + right facecam), 1.0 preserves all content. moments.json carries the reframe block per moment. zoom_crop/ clips are punched-in full-bleed (no bars).

### Next session starts with
- **Phase 5 — OpenCLIP + TwelveLabs** (per SPEC build order). Phase 4.5 is closed (detection + decision + track + zoom_crop dual output). Deferred: multi-person re-ID for track; promoting zoom_crop into the auto decision after the owner reviews dual output across real content.

### Commits
- (pending) "Phase 4.5 polish: bucket tune + reframe visibility + zoom_crop dual output"

### Files touched
- src/config.py, config.yaml, content_modes/shortform.yaml, src/render/social_formats.py, src/render/__init__.py, src/intelligence/ranking.py, tests/test_render.py, KNOWLEDGE.md, SESSION_LOG.md

---

## Session 7 (Phase 4.5 — Layer 1: subject detection) — 2026-05-28

**Phase:** Phase 4.5 — content-adaptive auto-reframe. **Layer 1 only** (detection); Layer 2 (decision) is the NEXT session.
**Goal going in:** foreground_scale 1.2 cropped needed MrBeast content → fixed framing is wrong; build detection that profiles each clip (count/spread) so a later decision engine picks method+params automatically. Detection first, verify, STOP before decision logic.

### What shipped
- **`ultralytics` YOLO dependency** added — torch stack UNCHANGED (still 2.5.1; only `+polars +psutil +ultralytics-thop`). Chose YOLO over MediaPipe for multi-person body counting (justified in KNOWLEDGE). `*.pt` weights gitignored (auto-download).
- **`analysis/subjects.py`** — pure profile math (`union_bbox`, `spread_ratio`, `profile_frame` → subject_count/group_bbox/spread_ratio/is_solo) + `detect_subjects` (samples frames via cv2, YOLO `person` class, lazy import, MPS+CPU fallback) → `analysis/subjects.json`. Wired into `run_analysis` (config-gated `subjects.enabled`, sequential).
- **Config:** `config.yaml subjects:` block + `SubjectsConfig` (enabled/model/device/sample_rate_hz/confidence).
- Tests-first: 7 pure profile tests; opt-in real-YOLO integration test (`CME_RUN_YOLO_MODEL=1`, like the WhisperX one).
- (Earlier post-Phase-4 render polish, same arc: composition zones `21a367d`, blur_pad compositor `973227c`, foreground_scale `431b167`.)

### Verification
- **164 passed, 2 skipped** (WhisperX + YOLO model opt-in); mypy (40 files) + ruff + format clean.
- **MrBeast.mp4 detection** (600 frames @1fps): subject_count median 1 / **max 7**, spread 0.03→**1.00** (median 0.46), **47% frames 2+ people**. Correctly distinguishes solo (spread ~0.05 → future `track`) from wide/busy (4 people spread 0.85 → future blur_pad fs~1.0). Foundation reads content correctly.

### Next session starts with
- **Phase 4.5 Layer 2** — `render/auto_reframe.py` decision engine: read `subjects.json` per moment → content profile → method + foreground_scale (DISCRETE BUCKETS per kkthedon: solo→track, 2→~1.3, 3-5→~1.1, 6+→1.0; none→blur_pad 1.0); one-euro smoothing for `track` crop-path; `reframe_method: auto` default. Do NOT start this session.

### Commits
- (pending) "Phase 4.5 Layer 1: YOLO subject detection -> subjects.json (content profile for auto-reframe)"

### Files touched
- src/analysis/subjects.py (new), src/analysis/__init__.py, src/config.py, config.yaml, pyproject.toml (+ultralytics, mypy override), uv.lock, .gitignore (*.pt), tests/test_analysis.py, KNOWLEDGE.md, SESSION_LOG.md

---

## Session 5 (Phase 4) — 2026-05-27

**Duration:** ~1 session
**Phase:** Phase 4 — Render Layer (shortform path)
**Goal going in:** Turn ranked moments into actual playable MP4 clips — shortform first (SPEC §14.4): cut → 9:16 reframe → burned captions → `src/output/shorts/`. Tests-first. Behind a render-backend abstraction (kkthedon kickoff requirement) so a future Descript backend slots in.

### What shipped
- **`render/backend.py`** — `RenderBackend` Protocol + backend-agnostic DTOs (`ClipRenderSpec`, `RenderFormat`, `CaptionLine`, `CaptionStyle`). ffmpeg confined to the concrete backend.
- **`ffmpeg_pipeline.py`** — `build_clip_command` (pure arg builder) + `FFmpegBackend.render_clip` (cut → reframe → burn ASS → encode; `h264_videotoolbox` with `libx264` fallback on `FFmpegError`).
- **`social_formats.py`** — `reframe_filter`: `center_crop` (default, full-bleed) + `blur_pad` (v1 = fit + black pad; blurred bg deferred). Unknown → warn + center_crop.
- **`subtitles.py`** — `group_words_into_lines` (≤`max_words_per_line` pop-on), `build_ass` (libass-burnable ASS), `resolve_font` (brand font if present, else system Helvetica/Arial + warn).
- **`render/__init__.py`** — `build_clip_specs` (pure: 60s cap, clip-relative captions, filename pattern) + `run_render` orchestrator (injectable backend); wired into `cme process` GATED to shortform.
- Stubs: `effects.py`, `transitions.py`, `color_pipeline.py`, `thumbnails.py`, `logo_animation.py`.
- `content_modes/shortform.yaml`: `reframe_method: center_crop`. Future-Enhancement notes (per-word karaoke, face-aware crop) in KNOWLEDGE.md.

### Verification (all green)
- pytest: **152 passed, 1 skipped** (`tests/test_render.py`: 13 unit + 1 `@pytest.mark.integration` real encode → 1080×1920 + duration + audio). mypy (39 files) + ruff check + format clean.
- **Smoke** `cme process tests/fixtures/MrBeast.mp4 --mode shortform --no-twelvelabs`: **10 playable MP4s** in `src/output/shorts/` (`20260528_MrBeast_clip_01..10.mp4`), each **1080×1920 h264 + aac, 60.0s**, ~46MB (6M bitrate). Font-fallback (Helvetica) + watermark-skip warnings fired as designed (SPEC §9). First watchable output. ⏳ kkthedon to eyeball caption sync + crop framing.

### What broke (and fixed)
- ruff: import sorting + an unused tempfile fd (closed it) + transient unused import (resolved by implementing the backend). All re-verified green.

### What I learned
- Brand assets (`src/branding/`) are entirely absent (gitignored owner IP) → render falls back to a system caption font and skips the watermark, both with warnings. Caption visibility isn't unit-asserted; the integration test checks geometry/duration/audio only.

### Blocked on
- Nothing. (Brand font + watermark assets would improve caption look + branding when added.)

### Next session starts with
- Phase 4 continued: **standard_longform** render path (16:9, separate-file SRT), then cinematic (LUT color grade) — or revisit shortform polish (per-word karaoke captions, true blur_pad, effects/transitions). Do NOT start in this session.

### Commits made this session
- (pending) "Phase 4: render layer working (shortform path)"

### Files touched
- src/render/ (new): backend.py, ffmpeg_pipeline.py, social_formats.py, subtitles.py, __init__.py (orchestrator), + stubs effects/transitions/color_pipeline/thumbnails/logo_animation.py
- src/cli.py (run_render wired, shortform-gated), content_modes/shortform.yaml (reframe_method)
- tests/test_render.py (new), README.md, KNOWLEDGE.md, SESSION_LOG.md (Session 5 + Session 4 backfill)

---

## Session 4 (Phase 3 patch + Phase 3.5) — 2026-05-27  *(backfilled)*

**Phase:** Phase 3 post-implementation patch + Phase 3.5 tuning
**Goal going in:** Fix the MPS alignment crash surfaced on a long real clip; add scoring diagnostic visibility + an empty-result fallback; then rework window aggregation so scores spread.

### What shipped
- **Alignment MPS→CPU fallback** (`whisper.py::align_transcript`, `CME_ALIGNMENT_DEVICE` override) for the wav2vec2 conv `Output channels > 65536` MPS limit on long audio. Reproduced + verified recovery on CPU; segment-length (not clip-length) triggered. Commit `80ea0df`.
- **Diagnostic visibility + fallback**: `moments.json` always reports `best_window` (per-signal `signal_contributions` + raw `signal_values`), replacing scalar `best_window_score`; top-N fallback (`top_n_fallback`, default 5) returns `below_threshold_best_candidates` + `used_fallback` when nothing clears `score_threshold` (never lowered). Commit `7485555`. Future-enhancement hook-tuning notes: `be2d200`.
- **Phase 3.5 window aggregation mean → 90th percentile** (`scoring.aggregate_window`, config `scoring.window_aggregation`/`window_percentile`, mode-overridable; `signal_means`→`signal_values` rename). MrBeast best window **0.361 → 0.744**, spread 0.11 → 0.28, 0 → 10 moments ≥0.55. Commit `f8b1999`.

### What I learned
- MrBeast (323 short WhisperX segments) aligns fine on MPS — the fallback only fires on a long segment.
- Mean-window aggregation was the structural score ceiling (~0.36); percentile fixed it. `score_threshold` held at 0.55 (now meaningful; revisit on calmer content).

### Commits made this session
- `80ea0df`, `7485555`, `be2d200`, `f8b1999`

### Files touched
- src/analysis/whisper.py, src/intelligence/{scoring,clustering,pacing_engine,ranking,__init__}.py, src/config.py, src/cli.py, config.yaml, content_modes/shortform.yaml, tests/{test_analysis,test_scoring}.py, README.md, KNOWLEDGE.md

---

## Session 3 (Phase 3) — 2026-05-26

**Duration:** ~1 session
**Phase:** Phase 3 — Intelligence + Scoring
**Goal going in:** Build `src/intelligence/` per SPEC §14 (scoring → mode composites → emotional → pacing → clustering → ranking → `moments.json`), tests-first, end-to-end with `--no-twelvelabs`.

### What shipped
- **Pre-implementation:** locked shortform clips to **60s flat** (min=max=preferred=60) for cross-platform monetization (commit `f8e93fc`); recorded Phase-3 design decisions (sliding-60s-window / audio-dynamics emotional proxy / per-run min-max) + rejected alternatives in KNOWLEDGE.md; created the top-level **Future Enhancements (Post-v1)** section (`shortform_reach` deferred).
- **scoring.py** — `normalize` (per-run min-max, flat→0), `make_grid`/`resample_curve`, signal primitives (`normalized_curve`, `influence_envelope`, `coverage_mask`), `redistribute_weights`, `composite_timeline`, DTOs (`WordSpan`/`Span`/`SignalData`/`ScoredTimeline`), generic `score_standard`. `MIN_WORD_CONFIDENCE=0.3`.
- **emotional_score.py** — `emotional_timeline` = min-max-normalized |ΔRMS| audio-dynamics proxy (no transcript; serves both shortform + cinematic). Interim, superseded in Phase 5.
- **viral_score.py** — shortform composite; word-confidence gating of hooks/quotables against `transcript.json` (drop words <0.3).
- **cinematic_score.py** — cinematic composite + `pacing_curve` motion+energy proxy; `openclip_aesthetic`+`soundtrack_sync` unavailable → redistributed.
- **pacing_engine.py** — mode `pacing`/`diversity` → `PacingPlan` (window/step/min-gap/threshold/cap).
- **clustering.py** — `slide_windows` (mean composite, sub-window sources clamp to one window) + `select_diverse` (greedy min-gap + per-minute cap).
- **ranking.py** — `snap_boundary` (graceful no-op without cuts), `rank_moments` (threshold/cap/snap/rank), `MomentsResult` schema + `write_moments`; `best_window_score` reported even when empty.
- **intelligence/__init__.py** — `run_intelligence` orchestrator (reads JSON → `SignalData` → mode scorer → slide → select → rank → write), wired into `cli.py process`.
- Tests: `tests/test_scoring.py` — 60 tests, all RED-first.

### Verification (all green)
- pytest: **117 passed, 1 skipped** (~32s), 91% coverage.
- mypy src/: clean (30 files). ruff check + format: clean.
- Smoke `cme process tests/fixtures/real_speech_sample.mp4 --mode shortform --no-twelvelabs`: produced valid `moments.json`. All 6 shortform signals available (no redistribution); single clamped 30s window scored **0.254 < 0.55** → **0 moments (honest empty)**, `best_window_score` recorded, threshold NOT lowered. Happy-path (ranked moment ≥0.55, contributions sum to score) covered by synthetic `run_intelligence` integration test.

### What broke (and fixed)
- ruff: en-dash in a CLI f-string (RUF001) → hyphen; missing `from pathlib import Path` in the test (F821). ruff-format normalized 3 files. All re-verified green.
- Design nuance corrected mid-build: event-presence signals (peaks/hooks→envelope, quotables→coverage) are constructed already in [0,1] and NOT re-min-maxed (avoids the flat→0 trap); only continuous magnitude curves get per-run min-max. KNOWLEDGE.md emotional bullet corrected from "lightly smoothed" to raw first-difference (matches code).

### What I learned
- WhisperX loads **pyannote** for VAD (not diarization) and logs benign pyannote-0.0.1/torch-1.10 version warnings + a lightning `weights_only=False` FutureWarning — all informational (recorded as a quirk).
- First real-output baseline: 30s real speech → shortform best window 0.254. Recorded in the shortform tuning log; do NOT tune from one short clip — need >60s footage so the window slides.
- SPEC §1 has no `standard_score.py`; standard_longform rides a generic `score_standard` path in scoring.py (semantic signals unavailable in Phase 3 → redistributed).

### Blocked on
- Nothing.

### Next session starts with
- Phase 4 — Render Layer (SPEC §8/§14): start with **shortform** rendering (single 9:16 aspect, burned captions), reading `moments.json`. Then standard_longform, cinematic last. Do NOT start Phase 4 in this session.

### Commits made this session
- `f8e93fc` "Pre-Phase 3: lock shortform clips to 60s flat for universal platform monetization"
- (pending) "Phase 3: intelligence + scoring layer working"

### Files touched
- src/intelligence/ (new): scoring.py, emotional_score.py, viral_score.py, cinematic_score.py, pacing_engine.py, clustering.py, ranking.py, __init__.py (orchestrator)
- src/cli.py (wired run_intelligence into process)
- tests/test_scoring.py (new, 60 tests)
- content_modes/shortform.yaml (60s lock), README.md, KNOWLEDGE.md, SESSION_LOG.md

---

## Session 2 (Phase 2) — 2026-05-25

**Duration:** ~1 session
**Phase:** Phase 2 — Analysis Layer
**Goal going in:** Build `src/analysis/` per SPEC §14 build order (librosa → opencv → whisper → hooks → aesthetics), each writing JSON to `workspace/[run-id]/analysis/`, tests-first, then wire orchestration into `cme process`.

### What shipped
- **librosa_analysis.py** — RMS energy curve, speech-band (300–3400 Hz) `speech_intensity`, global-z-score peak detection (1.5σ, 3s min-gap NMS) → `analysis/librosa.json`.
- **opencv.py** — frame-motion curve (sampled via grab/retrieve at `motion_sample_rate_hz`) + PySceneDetect ContentDetector scene cuts → `analysis/opencv.json`.
- **whisper.py** — WhisperX (faster-whisper CPU + wav2vec2 alignment on MPS), lazy-imported; pure `build_transcript` parser tested without the model → `transcript.json`.
- **hooks.py** — token-based hook-phrase matching (`within_window` = segment-leading) + quotable min-word heuristic → `analysis/hooks.json`.
- **aesthetics.py** — cv2 placeholders (lighting/composition/sharpness/colorfulness), all [0,1] → `analysis/aesthetics.json`.
- **src/analysis/__init__.py** — `run_analysis()` orchestrator + `should_transcribe()` mode gating; wired into `cli.py process`. Sequential (parallelism deferred — see What I learned).
- Tests: `tests/test_analysis.py` (37 tests, written RED-first), `conftest.py` `sample_audio` fixture, pyproject `filterwarnings`.

### Verification (all green)
- pytest: 57 passed, 1 skipped (opt-in WhisperX model test), ~53s.
- mypy src/: Success, 0 errors (benign unused-override note for Phase-5 modules).
- ruff check + format: clean.
- Smoke `cme process tests/fixtures/sample_60s_1080p.mp4`:
  - `--mode standard_longform` → `analysis/{librosa,opencv,aesthetics}.json`, no transcript (correct).
  - `--mode shortform` → also `transcript.json` (aligned=true) + `hooks.json`; WhisperX transcribe **and** align ran end-to-end (exit 0).

### What broke (and fixed)
- Test bug: `word.start or -1.0` → `-1.0` when start is `0.0` (falsy). Fixed the assertion, not the code (TDD).
- mypy: `librosa.load` returns sr as `int|float`, clashed with `sr: int` → stopped rebinding `sr`. ruff: dropped redundant `int(round(...))`.
- Discovered the cv2/av libavdevice dylib clash (objc stderr warning) — drove the sequential-analysis decision.

### What I learned
- cv2 + av ship clashing libavdevice dylibs on macOS → ProcessPoolExecutor (SPEC §5 parallelism) is risky under spawn; kept analysis **sequential**, deferred parallel to Phase 6.
- librosa.load pulls audioread → deprecated stdlib aifc/sunau/audioop on 3.11 → scoped pytest `filterwarnings` (not our code; didn't add soundfile as a direct dep).
- whisperx 3.4 real API is in submodules (`whisperx.asr/alignment/audio`); top-level re-exports show `(*args, **kwargs)`.
- The steady-tone fixture makes Whisper hallucinate one token ("you"), so the alignment branch actually runs — handy for verifying the path, but not alignment quality.

### Blocked on
- Nothing.

### Next session starts with
- Phase 3 — Intelligence + Scoring (SPEC §8/§14): `scoring.py` normalization first (synthetic data to verify math), then `viral_score.py`/`cinematic_score.py`/`emotional_score.py`, `pacing_engine.py`, `clustering.py`, `ranking.py` → `moments.json`. End-to-end with `--no-twelvelabs`.

### Commits made this session
- (pending) "Phase 2: analysis layer working"

### Files touched
- src/analysis/ (new): librosa_analysis.py, opencv.py, whisper.py, hooks.py, aesthetics.py, __init__.py (orchestrator)
- src/cli.py (wired run_analysis into process)
- tests/test_analysis.py (new), tests/conftest.py (sample_audio fixture)
- pyproject.toml (pytest filterwarnings), KNOWLEDGE.md, SESSION_LOG.md

---

## Session 1 (Phase 1) — 2026-05-24

**Duration:** ~1 session
**Phase:** Phase 1 — Foundation (scaffold + ingestion)
**Goal going in:** Stand up the env (uv + pre-commit), scaffold src/ per SPEC §1, ship a working ingestion layer verified by `cme process <video> --mode shortform`.

### What shipped
- Environment: `brew install uv` (0.11.16); `uv sync` on Python 3.11.15; `pre-commit install` (hooks live at .git/hooks/pre-commit).
- Package skeleton per SPEC §1: src/{utils,ingestion,analysis,intelligence,render}.
- utils/: timestamps (float HH:MM:SS.mmm), fileio (json + ensure_dir), ffmpeg (subprocess wrappers + ffprobe_json + FFmpegError), logging (Rich + file), progress (Rich Progress).
- config.py: full Pydantic schema for config.yaml (extra=forbid, exact match) + ModeConfig (common fields typed, extras allowed, scoring_weights-sum-to-1 validator); load_config / load_mode_config.
- ingestion/: metadata (ffprobe → MediaMetadata), validator (InputValidationError on missing/non-video/zero-duration), audio_extract (16kHz mono PCM wav), workspace (per-run dir + metadata.json), and the ingest() pipeline.
- cli.py: Typer `cme` with `process` wired to ingestion; `reprocess` + `workspace list/clean` stubbed.
- tests/: conftest generates a synthetic 60s/1080p fixture (gitignored); test_ingestion.py — 21 tests, written first (RED) then implemented (GREEN).
- Build: added hatchling [build-system] with packages=["src"] so `src` imports consistently in tests and the installed `cme` script.

### Verification (all green)
- pytest: 21 passed (7.7s).
- mypy src/: Success, 0 errors (benign note about unused Phase-2 override sections).
- ruff check src/: All checks passed.
- Smoke: `cme process tests/fixtures/sample_60s_1080p.mp4 --mode shortform` → exit 0; workspace/run_.../ with analysis/ + output/; audio.wav (16kHz mono, 60.0s); metadata.json (1920x1080, h264/aac, 60s).

### What broke (and fixed)
- `cme` entry initially failed (ModuleNotFoundError 'src'): default uv build exposed src/ contents as top-level modules, not `src` as a package. Fixed with hatchling packages=["src"].
- (Pre-Phase 1) Session-0 pyproject pins were unsatisfiable — see Pre-Phase 1 entry.

### What I learned
- src-as-package layout needs an explicit build backend mapping; the default uv_build src-layout assumes src/<project_name>/.
- Synthetic fixtures via conftest + gitignore keep the repo lean and tests reproducible.

### Blocked on
- Nothing.

### Next session starts with
- Phase 2 — Analysis Layer. Per SPEC §14: librosa_analysis.py first (simplest), then opencv.py, whisper.py (WhisperX), hooks.py, aesthetics.py. Each writes JSON to workspace/[run-id]/analysis/. Tests-first against the fixture.

### Commits made this session
- (pending) "Phase 1: scaffold + ingestion layer working"

### Files touched
- src/ (new): __init__.py ×6, cli.py, config.py, main.py, utils/{timestamps,fileio,ffmpeg,logging,progress}.py, ingestion/{__init__,metadata,validator,audio_extract,workspace}.py
- tests/: conftest.py, test_ingestion.py, __init__.py
- pyproject.toml (hatchling build-system), .gitignore (fixtures), .python-version, uv.lock
- knowledge.md, SESSION_LOG.md

---

## Pre-Phase 1 — 2026-05-24

**Duration:** Short — decision + config lock-in
**Phase:** Pre-Phase 1 (no source code yet)
**Goal going in:** Evaluate transcription options against the prior clip-pipeline / content_business projects, then lock the transcription stack before writing Phase 2's whisper module.

### What shipped
- Locked transcription stack: **WhisperX** (faster-whisper CTranslate2 backbone + wav2vec2 forced alignment). NOT openai-whisper; NOT MLX-Whisper as primary.
- Config locked: language=en, VAD on, condition_on_previous_text=False, model=small, ffmpeg loudnorm pre-pass, float timestamps end-to-end, no pyannote/diarization. Whisper step CPU-only on M1; alignment on MPS.
- Mode-conditional transcription: shortform always on; cinematic_longform + standard_longform off by default, opt-in via --transcribe.
- standard_longform SRT output gated behind --transcribe (YouTube auto-captions cover default SEO).
- MLX-Whisper documented as a v2 fast-path (KNOWLEDGE.md Open Questions) — not implemented.
- git init in project root; .gitignore created (verified .env is ignored, not staged).
- Updated CLAUDE.md, SPEC.md, pyproject.toml, config.yaml, all three content_modes/*.yaml, KNOWLEDGE.md.

### What broke
- Nothing. Found and resolved a config conflict: standard_longform declared an SRT output while transcription is off-by-default → resolved by gating the SRT behind --transcribe.

### What I learned
- Prior project (content_business) bumped Whisper small→medium and built a karaoke-span render hack solely to mask word-timing jitter — wav2vec2 alignment makes both unnecessary.
- CTranslate2 has no Metal backend (still true 2026): faster-whisper is CPU-only on Apple Silicon. Recorded as a library quirk.

### Blocked on
- Nothing. Pre-commit hooks intentionally deferred until Phase 1 dependency install (uv sync + pre-commit install).

### Next session starts with
- Begin Phase 1: run `uv sync`, then `pre-commit install` (first actions). Then scaffold + config.py + cli.py stubs + utils + ingestion, tests-first.

### Commits made this session
- (pending approval) "Pre-Phase 1: lock transcription stack (WhisperX, mode-conditional, MLX-Whisper future fast-path)"

### Files touched
- .gitignore (new), claude.md, spec.md, pyproject.toml, config.yaml, content_modes/shortform.yaml, content_modes/cinematic_longform.yaml, content_modes/standard_longform.yaml, knowledge.md, SESSION_LOG.md

---

## Session 0 — 2026-05-24

**Duration:** Initial setup
**Phase:** Pre-Phase 1 (foundation files)
**Goal going in:** Generate project foundation files and set up tooling

### What shipped
- SPEC.md created (full project spec, 14 sections)
- CLAUDE.md created (project rules and error prevention stack)
- KNOWLEDGE.md created (template for evolving project memory)
- pyproject.toml created (deps pinned, mypy/ruff/pytest configured)
- .pre-commit-config.yaml created (hooks for mypy + ruff + pytest)
- config.yaml created (root global config)
- standard_longform.yaml, cinematic_longform.yaml, shortform.yaml created (mode configs)
- MCP_SETUP.md created (install guide for 5 core MCPs)
- This SESSION_LOG.md created

### What broke
- Initial Filesystem MCP install had typo (`/Usere/` instead of `/Users/`) — fixed by removing and re-adding
- Project folder name confusion (`cinematic-media-engine` vs `Longformcontentproject`) — resolved by using actual folder name

### What I learned
- Claude Code MCP install syntax requires `--transport stdio` flag and `--` separator
- 40-tool ceiling exists across all MCPs combined — stick to 5 core MCPs
- Filesystem MCP fails to connect if ANY of the listed paths don't exist
- SSE transport is being deprecated; stdio is correct for local MCPs

### Blocked on
- Nothing currently. Ready to start Phase 1.

### Next session starts with
- Verify all 5 MCPs are connected (`claude mcp list`)
- Initialize git repo in `/Users/donaldk/Desktop/Longformcontentproject`
- Tell Claude Code: "Read SPEC.md, CLAUDE.md, KNOWLEDGE.md, and SESSION_LOG.md. Begin Phase 1: project scaffold and ingestion layer."

### Commits made this session
- (No commits yet — foundation files only, project not yet initialized in git)

### Files generated (downloaded from Claude.ai outputs)
- SPEC.md
- CLAUDE.md
- KNOWLEDGE.md
- SESSION_LOG.md (this file)
- MCP_SETUP.md
- pyproject.toml
- .pre-commit-config.yaml
- config.yaml
- content_modes/standard_longform.yaml
- content_modes/cinematic_longform.yaml
- content_modes/shortform.yaml

### MCPs installed
- filesystem ✓ Connected
- memory ✓ Connected
- context7 [verify connection]
- github [pending — token security update needed]
- sequential-thinking [pending]

---

## (Add new sessions above this line — most recent first)
