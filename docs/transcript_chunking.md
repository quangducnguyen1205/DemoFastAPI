# Transcript Chunking

This service still emits the same downstream transcript shape: ordered plain-text rows with `video_id`, `segment_index`, and `text`. The change in this repo is only how those text rows are formed before they are persisted and embedded.

Relevant code paths:
- `backend/app/tasks/video_tasks.py`
- `backend/app/services/video_processing.py`
- `backend/app/utils.py`

## Previous behavior

Before this update, `split_transcript_text()` grouped sentence-like fragments into chunks up to `200` characters and then hard-sliced any oversized fragment by raw character count. There was no overlap between adjacent chunks.

That was simple and deterministic, but it had two practical downsides for search:
- a relevant lecture phrase could be split across two stored transcript rows
- long run-on transcription fragments could be cut in the middle of a word

## Current behavior

The current chunker is still deterministic and intentionally lightweight, but it is more retrieval-friendly:

- Chunking unit: sentence-like fragments split on `.`, `!`, `?`, and line breaks
- Boundary rule: greedily group adjacent fragments up to `450` characters
- Overlap: carry the final sentence from the previous chunk into the next chunk when it still fits
- Long-fragment fallback: wrap oversized fragments on word boundaries with an `8`-word overlap
- Output contract: unchanged `List[str]` chunks, persisted as sequential `segment_index` rows

## Why this is better for the current product

For the current search-first product, retrieval quality depends heavily on whether a useful phrase survives inside a single stored segment.

- Better lexical/phrase retrieval: larger sentence-aware chunks reduce accidental splitting of important terms across segment boundaries.
- Better boundary continuity: repeating one sentence into the next chunk gives phrase-boosted search a second chance when a concept spans two neighboring chunks.
- Better retrieval precision than very large windows: the chunker still keeps segments relatively compact instead of merging entire transcript sections into broad mixed-topic blocks.
- Better future vector compatibility: sentence-coherent chunks are cleaner embedding inputs than blind character slices.

## Intentionally not implemented

This change does **not** attempt to redesign the product flow.

- No timestamp-aware media segmentation
- No speaker attribution
- No semantic segmentation model
- No transcript schema change
- No search API contract change
