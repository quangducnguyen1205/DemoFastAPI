import re
from typing import List


DEFAULT_TRANSCRIPT_CHUNK_CHARS = 450
DEFAULT_TRANSCRIPT_CHUNK_OVERLAP_SENTENCES = 1
DEFAULT_TRANSCRIPT_LONG_SENTENCE_OVERLAP_WORDS = 8

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _split_sentence_like_units(text: str) -> List[str]:
    return [
        normalized
        for part in _SENTENCE_BOUNDARY_RE.split(text.strip())
        if (normalized := _normalize_whitespace(part))
    ]


def _wrap_long_fragment(text: str, max_len: int, overlap_words: int) -> List[str]:
    """Wrap oversized fragments on word boundaries with a small word overlap."""
    normalized = _normalize_whitespace(text)
    if not normalized:
        return []
    if len(normalized) <= max_len:
        return [normalized]

    words = normalized.split()
    chunks: List[str] = []
    start = 0

    while start < len(words):
        current_words: List[str] = []
        current_len = 0
        index = start

        while index < len(words):
            word = words[index]
            candidate_len = current_len + (1 if current_words else 0) + len(word)
            if current_words and candidate_len > max_len:
                break
            if not current_words and len(word) > max_len:
                current_words.append(word)
                index += 1
                break
            current_words.append(word)
            current_len = candidate_len
            index += 1

        if not current_words:
            break

        chunks.append(" ".join(current_words))
        if index >= len(words):
            break

        if overlap_words > 0 and len(current_words) > 1:
            next_start = max(
                index - min(overlap_words, len(current_words) - 1),
                start + 1,
            )
        else:
            next_start = index

        if next_start <= start:
            next_start = index
        start = next_start

    return chunks


def split_transcript_text(
    text: str,
    max_len: int = DEFAULT_TRANSCRIPT_CHUNK_CHARS,
    *,
    overlap_sentences: int = DEFAULT_TRANSCRIPT_CHUNK_OVERLAP_SENTENCES,
    long_sentence_overlap_words: int = DEFAULT_TRANSCRIPT_LONG_SENTENCE_OVERLAP_WORDS,
) -> List[str]:
    """Split transcript text into deterministic, sentence-aware search chunks.

    Strategy:
    - normalize whitespace and split on sentence-like boundaries (`.`, `!`, `?`,
      and line breaks)
    - greedily group adjacent units until the max character budget is reached
    - carry over a small sentence overlap when the next chunk can fit it
    - wrap oversized single fragments on word boundaries instead of blind
      character slicing
    """
    if not text:
        return []
    if max_len <= 0:
        raise ValueError("max_len must be positive")

    units = _split_sentence_like_units(text)
    if not units:
        return []

    chunks: List[str] = []
    current_units: List[str] = []
    current_len = 0

    for unit in units:
        if len(unit) > max_len:
            if current_units:
                chunks.append(" ".join(current_units))
                current_units = []
                current_len = 0
            chunks.extend(
                _wrap_long_fragment(
                    unit,
                    max_len=max_len,
                    overlap_words=long_sentence_overlap_words,
                )
            )
            continue

        candidate_len = len(unit) if not current_units else current_len + 1 + len(unit)
        if candidate_len <= max_len:
            current_units.append(unit)
            current_len = candidate_len
            continue

        chunks.append(" ".join(current_units))

        overlap_units: List[str] = []
        if overlap_sentences > 0 and current_units:
            max_overlap = min(overlap_sentences, len(current_units))
            for count in range(max_overlap, 0, -1):
                candidate_overlap = current_units[-count:]
                overlap_text = " ".join(candidate_overlap)
                if len(overlap_text) + 1 + len(unit) <= max_len:
                    overlap_units = candidate_overlap
                    break

        current_units = list(overlap_units)
        current_len = len(" ".join(current_units)) if current_units else 0

        if current_units:
            current_units.append(unit)
            current_len = current_len + 1 + len(unit)
        else:
            current_units = [unit]
            current_len = len(unit)

    if current_units:
        chunks.append(" ".join(current_units))

    return chunks
