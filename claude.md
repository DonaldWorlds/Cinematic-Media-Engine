# CLAUDE.md — Cinematic Media Engine

This file is auto-loaded by Claude Code at the start of every session. It defines the rules for working on this project. Read it fully before doing anything else.

---

## Project Context

This is the **Cinematic Media Engine** — a multi-style media intelligence system that ingests video and produces long-form videos, short-form clips, and AI-generated thumbnails. Three content modes: standard_longform, cinematic_longform, shortform.

**Owner:** kkthedon
**Source of truth:** SPEC.md (read this first, follow it strictly)

---

## Hardware Target

- **Primary:** Mac M1 MacBook
- **Streaming/gaming PC:** Windows + GTX 1050 Ti (used for source content capture, not for running this engine)
- Build for M1. Metal acceleration via PyTorch MPS backend where applicable.
- Test all GPU code paths with CPU fallback. Never assume CUDA.

---

## Language & Dependencies

- **Python 3.11+** required
- **Dependency management: uv** — never use pip directly, never use poetry
- **All API keys via .env** — never hardcoded, never committed
- **Prefer standard library** over new dependencies
- **Justify any new dependency** before adding it — if it's not in SPEC.md, ask first

### Core dependencies (pinned in pyproject.toml)

- `whisperx` — the required transcription stack: faster-whisper (CTranslate2) backbone for the transcript + wav2vec2 forced alignment for word timestamps (<100ms vs ~1s drift on raw Whisper). Still CTranslate2 under the hood — NEVER openai-whisper. Locked config: `language=en`, VAD on, `condition_on_previous_text=False`, `model=small` (precision comes from alignment, not size), ffmpeg `loudnorm` pre-pass, float timestamps end-to-end. Mode-conditional (shortform always; cinematic + standard opt-in via `--transcribe`). Diarization (pyannote) is NOT used — single creator, no HF token. On M1 the Whisper step is CPU-only (CTranslate2 has no Metal backend); wav2vec2 alignment uses MPS. MLX-Whisper is a documented future fast-path for non-timestamp-critical bulk long-form — NOT implemented now.
- `torch` / `torchaudio` — wav2vec2 alignment models for WhisperX (MPS-capable on M1)
- `ffmpeg` — system binary, called via subprocess through `src/utils/ffmpeg.py`
- `librosa` — audio analysis
- `opencv-python` — motion and scene detection
- `open-clip-torch` — aesthetic scoring
- `twelvelabs` — semantic video API client
- `typer` — CLI framework
- `pydantic` — config validation
- `rich` — terminal output and progress

### Dev dependencies

- `pytest` — testing
- `mypy` — type checking
- `ruff` — linting and formatting
- `pre-commit` — git hooks

---

## Build Order (STRICT)

Follow SPEC.md Section 14 build order exactly. Do not skip ahead.

1. Phase 1 — Foundation (scaffold, CLI, config, ingestion)
2. Phase 2 — Analysis Layer
3. Phase 3 — Intelligence + Scoring
4. Phase 4 — Render Layer
5. Phase 5 — OpenCLIP + TwelveLabs
6. Phase 6 — Polish

**Phase N must work end-to-end before starting Phase N+1.**

After each phase:
1. Run all tests — must pass
2. Run mypy — must pass with zero errors
3. Run ruff check — must pass with zero warnings
4. Commit with message `Phase N: <description>`
5. **End the Claude Code session.** Start a new session for the next phase.

---

## Error Prevention Stack

After EVERY code change:

1. Run `mypy src/` — must pass with zero errors
2. Run `ruff check src/` — must pass with zero warnings
3. Run `pytest tests/` — all tests must pass

Do NOT proceed to the next module if any of these fail. Fix immediately.

Before committing:
- Pre-commit hooks must pass (mypy + ruff + pytest run automatically)
- Do NOT bypass with `--no-verify` under any circumstance

---

## Testing Discipline

- **Tests are written FIRST**, before the module they test
- Write the test, watch it fail, then write the code to make it pass
- Use the fixture videos in `tests/fixtures/` — never write tests against imaginary inputs
- If a test fails twice in a row, STOP. Re-read SPEC.md. Ask before continuing.
- Fix code to match tests. Do NOT modify tests to match broken code unless the test is demonstrably wrong.

---

## When to Stop and Ask

Stop coding and ask the user (kkthedon) BEFORE doing any of these:

- Adding any dependency not listed in SPEC.md or CLAUDE.md
- Deviating from the architecture in SPEC.md Section 1
- Changing the config schema in config.yaml or content_modes/*.yaml
- Modifying public function signatures of completed modules
- Refactoring code that's already passing tests
- When a phase's tests fail twice in a row
- When you think the spec is wrong about something
- When you encounter a design decision not covered in SPEC.md

**Default behavior on ambiguity: ask. Do not improvise.**

---

## Code Style

- **Type hints required on all functions** — including return types
- **Docstrings on all public functions** — short, describing what and why
- **No comments explaining what code does** — code should be self-documenting
- **Comments only for why** — non-obvious decisions, gotchas, references
- **Pydantic models for all config and data structures** — no raw dicts passed between modules
- **Use `pathlib.Path`** — never use string paths or `os.path`
- **Use `subprocess.run` with `check=True`** for shell calls — never use `os.system`
- **Use structured logging** via `src/utils/logging.py` — never use `print` in production code (CLI output via Rich is fine)

---

## Architectural Rules

1. **Modules don't reach across layers.** Ingestion doesn't know about scoring. Scoring doesn't know about rendering. Each layer reads/writes through the workspace.

2. **Workspace is the contract.** Each analysis module writes to `workspace/[run-id]/analysis/<name>.json`. Each downstream module reads from there. No in-memory state passed between layers.

3. **Mode-aware behavior lives in config, not code.** Adding a fourth content mode = new yaml file + new scoring profile. No architectural changes.

4. **TwelveLabs and OpenCLIP are optional signals.** The system must run end-to-end without them. If either fails or is disabled, redistribute weights to remaining signals.

5. **Parallel where possible.** Audio analysis, motion detection, aesthetic scoring don't depend on each other. Use `concurrent.futures.ProcessPoolExecutor`.

6. **TwelveLabs upload kicks off FIRST.** It's API-bound and slow. Start the upload, then run local analysis while it indexes.

---

## File and Folder Rules

- **Never create files outside the structure defined in SPEC.md Section 1**
- **Never put output files in the repo root** — they go to `src/output/` subdirectories
- **Workspace is gitignored** — never commit workspace contents
- **Brand assets are gitignored** — LUTs, logos, fonts are owner's IP, don't commit them
- **.env is gitignored** — never commit API keys

---

## Never-Touch Files

These files must never be edited or deleted by a coding task. Editing one is allowed
ONLY when the user explicitly authorizes it for that specific task (as with this very
section being added).

- **claude.md** (this file) — project rules. Change only when the user asks to change the rules.
- **POST_PRODUCTION_INTELLIGENCE_SPEC.md** — never edit, never delete.
- **dont_read_this_yet.md** — never read, never edit.
- **message.md** — never read, never edit.

Why: a prior session went out of scope during an unrelated tune and edited claude.md +
deleted POST_PRODUCTION_INTELLIGENCE_SPEC.md (reverted via `git reset --hard`). Scope
discipline: a task touches only the files that task needs. If a change appears to require
editing one of these, STOP and ask.

---

## Session Hygiene

- **One phase per session.** Do not try to ship multiple phases in one Claude Code session.
- **Fresh session per phase** keeps context clean and quality high.
- **At session start:** Read SPEC.md (relevant section for current phase), read CLAUDE.md (this file), check git log to see what's committed.
- **At session end:** Commit, summarize what shipped, what's next, save state to KNOWLEDGE.md if relevant.

---

## KNOWLEDGE.md

Maintain a `KNOWLEDGE.md` file in the repo root for evolving project state:

- Decisions made and WHY
- Things tried that didn't work
- Library quirks discovered
- TwelveLabs query phrasings that work well
- Tuned weight values per content mode
- Performance benchmarks observed

Update KNOWLEDGE.md whenever a non-trivial decision is made. Read it at the start of every session.

---

## Tooling Setup

This project assumes the following MCPs are installed and active:

- **Filesystem MCP** — read/write access to project directory
- **Memory MCP** — persistent context across sessions
- **Context7 MCP** — current library docs at request time
- **GitHub MCP** — repo and history access

If any of these are missing, ask the user before proceeding.

---

## Output Discipline

- **No verbose explanations of what you're about to do.** Just do it.
- **No "I'll start by..." preamble.** Start.
- **No progress narration during multi-step work.** Do the work, report results.
- **Use Rich progress bars for long-running work** — that's the user-facing progress signal.
- **Logs go to file, not console**, except CLI output via Rich.

---

## What Done Means

A phase is "done" when:

1. All planned modules are implemented
2. All tests pass (`pytest tests/`)
3. Type checks pass (`mypy src/`)
4. Lint passes (`ruff check src/`)
5. End-to-end smoke test runs successfully on a fixture video
6. README is updated with how to run what was built
7. Commit is made with a clear message
8. KNOWLEDGE.md is updated with any non-obvious decisions

If you can't write clear README instructions for what you just built, it isn't done.

---

## What This Project Is NOT

- This is NOT a real-time clipping system. The F8/F9 manual clipper is a separate project. Do not modify that workflow.
- This is NOT a distribution system. Auto-upload to platforms is out of scope.
- This is NOT a content recommendation engine. It produces files, not suggestions.
- This is NOT for sale. It's an internal tool for kkthedon's content production. Optimize for one user (the owner), not for product polish.

---

## Final Rule

**When in doubt, re-read SPEC.md. If the answer isn't there, ask the user. Never improvise architecture.**

The goal is shipping a working engine, not demonstrating cleverness. Boring, predictable, well-tested code that follows the spec exactly beats elegant code that drifts from the plan.
