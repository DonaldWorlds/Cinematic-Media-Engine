"""Thumbnail text suggestions — short quotable phrases for the cut window.

Phase 7 (standard_longform / Descript-prep): the engine surfaces 3-7 word
quotable phrases drawn from the transcript inside the selected window and writes
them to moments.json as ``thumbnail_text_suggestions``. Descript (or a human)
picks the actual thumbnail text downstream; the engine only suggests.

An explicit status lets a consumer distinguish empty-because-no-transcript from
empty-because-no-quotable-content:

- ``transcript_unavailable`` — no transcript ran (standard_longform without --transcribe)
- ``no_quotable_phrases_found`` — transcript present but nothing qualified in the window
- ``ok`` — suggestions produced
"""

from __future__ import annotations

from pydantic import BaseModel

from .scoring import MIN_WORD_CONFIDENCE

MIN_PHRASE_WORDS = 3
MAX_PHRASE_WORDS = 7

# (text, start_sec, end_sec, word_confidence)
Word = tuple[str, float, float, float]
# (start_sec, end_sec)
QuotableSpan = tuple[float, float]


class ThumbnailText(BaseModel):
    """Thumbnail text suggestions for one cut window + why the list is what it is."""

    suggestions: list[str]
    status: str


def _best_run(words: list[Word]) -> tuple[list[str], float] | None:
    """Highest-mean-confidence 3-7 word contiguous run within one quotable's words.

    Words below ``MIN_WORD_CONFIDENCE`` are dropped first (Phase-3 word gating). Returns
    the chosen tokens + their mean confidence, or ``None`` when fewer than
    ``MIN_PHRASE_WORDS`` confident words remain.
    """
    confident = [w for w in words if w[3] >= MIN_WORD_CONFIDENCE]
    if len(confident) < MIN_PHRASE_WORDS:
        return None
    if len(confident) <= MAX_PHRASE_WORDS:
        return [w[0] for w in confident], sum(w[3] for w in confident) / len(confident)
    best: tuple[list[str], float] | None = None
    for size in range(MIN_PHRASE_WORDS, MAX_PHRASE_WORDS + 1):
        for i in range(len(confident) - size + 1):
            run = confident[i : i + size]
            mean = sum(w[3] for w in run) / size
            if best is None or mean > best[1]:
                best = ([w[0] for w in run], mean)
    return best


def extract_thumbnail_text(
    *,
    transcript_available: bool,
    words: list[Word],
    quotables: list[QuotableSpan],
    window_start: float,
    window_end: float,
    count: int = 3,
) -> ThumbnailText:
    """Top ``count`` short quotable phrases from the transcript inside the window.

    One candidate phrase is built per quotable span overlapping the window, using only
    the span's words that fall inside the window; phrases are ranked by mean word
    confidence and de-duplicated.
    """
    if not transcript_available:
        return ThumbnailText(suggestions=[], status="transcript_unavailable")

    scored: list[tuple[float, str]] = []
    for q_start, q_end in quotables:
        if q_end < window_start or q_start > window_end:
            continue
        lo, hi = max(q_start, window_start), min(q_end, window_end)
        span_words = [w for w in words if w[1] >= lo and w[2] <= hi]
        run = _best_run(span_words)
        if run is None:
            continue
        tokens, mean = run
        scored.append((mean, " ".join(tokens)))

    if not scored:
        return ThumbnailText(suggestions=[], status="no_quotable_phrases_found")

    scored.sort(key=lambda s: s[0], reverse=True)
    suggestions: list[str] = []
    for _, phrase in scored:
        if phrase not in suggestions:
            suggestions.append(phrase)
        if len(suggestions) >= count:
            break
    return ThumbnailText(suggestions=suggestions, status="ok")
