"""Transcript-based hook-phrase and quotable detection.

Feeds the shortform scoring signals: ``hooks`` (viral_hooks) and ``quotables``
(quotable_dialogue). Hook phrases come from config; a hit ``within_window`` means
it lands inside the segment's first ``hook_window_sec`` (the attention zone).

Matching is token-based, not raw substring, so "no way" does not match "norway".
The quotable heuristic (min word count) is a Phase-2 placeholder to tune on real
footage — see KNOWLEDGE.md.
"""

from __future__ import annotations

import string
from pathlib import Path

from pydantic import BaseModel

from ..config import HooksConfig
from ..utils.fileio import ensure_dir, write_json
from .whisper import TranscriptResult

DEFAULT_MIN_QUOTABLE_WORDS = 5


class HookHit(BaseModel):
    """One hook-phrase occurrence located in the transcript."""

    phrase: str
    time_sec: float
    segment_index: int
    within_window: bool


class Quotable(BaseModel):
    """A segment that reads as a self-contained quotable line."""

    text: str
    start_sec: float
    end_sec: float
    word_count: int


class HooksAnalysis(BaseModel):
    """Hook + quotable detections for one source, written to hooks.json."""

    hooks: list[HookHit]
    quotables: list[Quotable]
    hook_count: int
    quotable_count: int


def _tokens(text: str) -> list[str]:
    """Lowercased word tokens with surrounding punctuation stripped."""
    out = []
    for raw in text.split():
        token = raw.strip(string.punctuation).lower()
        if token:
            out.append(token)
    return out


def _sublist_index(haystack: list[str], needle: list[str]) -> int | None:
    """Index of the first contiguous occurrence of ``needle`` in ``haystack``."""
    if not needle or len(needle) > len(haystack):
        return None
    for i in range(len(haystack) - len(needle) + 1):
        if haystack[i : i + len(needle)] == needle:
            return i
    return None


def find_hooks(
    transcript: TranscriptResult, hook_phrases: list[str], window_sec: float
) -> list[HookHit]:
    """Locate configured hook phrases in the transcript (one hit per phrase/segment)."""
    phrases = [(p, _tokens(p)) for p in hook_phrases]
    hits: list[HookHit] = []
    for index, segment in enumerate(transcript.segments):
        word_tokens = [(_tokens(w.word), w) for w in segment.words]
        flat = [(tok, word) for toks, word in word_tokens for tok in toks]
        token_list = [tok for tok, _ in flat]
        for phrase, ptoks in phrases:
            if not ptoks:
                continue
            if segment.words:
                at = _sublist_index(token_list, ptoks)
                if at is None:
                    continue
                start_word = flat[at][1]
                time_sec = start_word.start if start_word.start is not None else segment.start
            else:
                if _sublist_index(_tokens(segment.text), ptoks) is None:
                    continue
                time_sec = segment.start
            hits.append(
                HookHit(
                    phrase=phrase,
                    time_sec=time_sec,
                    segment_index=index,
                    within_window=(time_sec - segment.start) <= window_sec,
                )
            )
    return hits


def find_quotables(
    transcript: TranscriptResult, min_words: int = DEFAULT_MIN_QUOTABLE_WORDS
) -> list[Quotable]:
    """Segments with at least ``min_words`` words, treated as quotable lines."""
    quotables: list[Quotable] = []
    for segment in transcript.segments:
        count = len(_tokens(segment.text))
        if count >= min_words:
            quotables.append(
                Quotable(
                    text=segment.text,
                    start_sec=segment.start,
                    end_sec=segment.end,
                    word_count=count,
                )
            )
    return quotables


def analyze_hooks(transcript: TranscriptResult, config: HooksConfig) -> HooksAnalysis:
    """Run hook + quotable detection over a transcript."""
    hooks = find_hooks(transcript, config.hook_phrases, config.hook_window_sec)
    quotables = find_quotables(transcript)
    return HooksAnalysis(
        hooks=hooks,
        quotables=quotables,
        hook_count=len(hooks),
        quotable_count=len(quotables),
    )


def write_hooks_json(analysis: HooksAnalysis, analysis_dir: Path) -> Path:
    """Write the analysis to ``<analysis_dir>/hooks.json``; return its path."""
    out = ensure_dir(analysis_dir) / "hooks.json"
    write_json(out, analysis.model_dump())
    return out
