from app.services.video_processing import segment_text
from app.utils import split_transcript_text


def test_segment_text_defaults_to_larger_sentence_aware_budget():
    text = (
        "Sentence one introduces lexical retrieval tradeoffs in lecture search. "
        "Sentence two keeps the same idea together so a practical search segment "
        "should not be split too early."
    )

    chunks = segment_text(text)

    assert chunks == [text]


def test_split_transcript_text_reuses_last_sentence_for_boundary_overlap():
    text = (
        "Alpha explains retrieval. "
        "Beta carries the boundary phrase. "
        "Gamma finishes the example."
    )

    chunks = split_transcript_text(text, max_len=75)

    assert chunks == [
        "Alpha explains retrieval. Beta carries the boundary phrase.",
        "Beta carries the boundary phrase. Gamma finishes the example.",
    ]


def test_split_transcript_text_wraps_overlong_fragments_on_word_boundaries():
    text = (
        "This is a deliberately overlong sentence about lexical retrieval quality "
        "and phrase continuity that should never be chopped in the middle of a "
        "word even when the chunk budget is tight and the sentence keeps going "
        "for a while without punctuation"
    )

    chunks = split_transcript_text(text, max_len=80)

    assert chunks == [
        "This is a deliberately overlong sentence about lexical retrieval quality and",
        "deliberately overlong sentence about lexical retrieval quality and phrase",
        "overlong sentence about lexical retrieval quality and phrase continuity that",
        "about lexical retrieval quality and phrase continuity that should never be",
        "quality and phrase continuity that should never be chopped in the middle of a",
        "never be chopped in the middle of a word even when the chunk budget is tight and",
        "even when the chunk budget is tight and the sentence keeps going for a while",
        "and the sentence keeps going for a while without punctuation",
    ]
    assert all(len(chunk) <= 80 for chunk in chunks)


def test_split_transcript_text_keeps_short_sections_as_single_chunk():
    text = "Short lecture summary without extra splitting."

    assert split_transcript_text(text, max_len=120) == [text]


def test_split_transcript_text_is_deterministic():
    text = (
        "First sentence explains embeddings. "
        "Second sentence covers lexical phrase boosts. "
        "Third sentence links both ideas back to lecture retrieval."
    )

    first = split_transcript_text(text, max_len=90)
    second = split_transcript_text(text, max_len=90)

    assert first == second
