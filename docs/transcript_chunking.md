# Transcript Chunking

Repo A still persists transcript output as ordered plain-text rows with:

- `video_id`
- `segment_index`
- `text`

That output shape is intentionally stable for Repo B integration.

## Current behavior

Transcript chunking is deterministic and text-based:

- split into sentence-like units on `.`, `!`, `?`, and line breaks
- greedily group units up to `450` characters
- reuse the last sentence as overlap when it still fits in the next chunk
- wrap oversized fragments on word boundaries with an `8`-word overlap

Implementation lives in:

- `backend/app/utils.py`
- `backend/app/services/video_processing.py`
- `backend/app/tasks/video_tasks.py`

## Why this shape still exists

Repo A is a processing service, not a full transcript understanding system. The current chunker exists to:

- keep transcript rows readable and deterministic
- avoid blind character cuts in long fragments
- give Repo B cleaner transcript segments to store, display, or index

## Intentionally not implemented

- timestamp-aware media segmentation
- speaker attribution
- chunk-level semantic search
- transcript editing
- schema changes beyond ordered transcript rows
