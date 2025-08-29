from typing import List


def split_transcript_text(text: str, max_len: int = 200) -> List[str]:
    """Split text into sentence-like chunks and group to <= max_len characters.

    Strategy: split on '.', '!', '?' keeping the delimiter, then aggregate
    consecutive sentences into chunks not exceeding max_len. If a sentence is
    longer than max_len, hard-wrap it.
    """
    if not text:
        return []
    import re
    parts = re.split(r"([.!?])", text)
    sentences: List[str] = []
    for i in range(0, len(parts), 2):
        sent = parts[i].strip()
        if not sent:
            continue
        delim = parts[i + 1] if i + 1 < len(parts) else ""
        sentences.append((sent + delim).strip())

    chunks: List[str] = []
    current = ""
    for s in sentences:
        if not current:
            current = s
        elif len(current) + 1 + len(s) <= max_len:
            current = f"{current} {s}"
        else:
            chunks.append(current)
            current = s
    if current:
        chunks.append(current)

    wrapped: List[str] = []
    for ch in chunks:
        if len(ch) <= max_len:
            wrapped.append(ch)
        else:
            for i in range(0, len(ch), max_len):
                wrapped.append(ch[i:i+max_len])
    return wrapped
