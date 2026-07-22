# Transcript Chunking

Repo A persists Kafka-originated processing artifacts as ordered transcript rows with:

- `processing_request_event_id`
- `segment_index`
- nullable `start_ms`
- nullable `end_ms`
- `text`

`start_ms` and `end_ms` are integer milliseconds. A row has either both values absent, or
both present with `start_ms >= 0` and `end_ms >= start_ms`. Legacy rows remain readable as
`null`; the service never derives timing from row order or text.

## Current behavior

Whisper's structured segments are now the primary processing boundary. The Whisper adapter:

- reads provider `start`/`end` values expressed as finite seconds
- converts each value once with `round(seconds * 1000)`
- rejects partial, negative, non-finite, or backwards timing
- preserves provider segment order as `segment_index`

When the provider supplies no structured segments, the compatibility fallback remains
deterministic and text-based:

- split into sentence-like units on `.`, `!`, `?`, and line breaks
- greedily group units up to `450` characters
- reuse the last sentence as overlap when it still fits in the next chunk
- wrap oversized fragments on word boundaries with an `8`-word overlap

Implementation lives in:

- `backend/app/utils.py`
- `backend/app/services/video_processing.py`
- `backend/app/processing/adapters/whisper_transcriber.py`

## Why this shape still exists

Repo A is a processing service, not a full transcript understanding system. The current chunker exists to:

- keep transcript rows readable and deterministic
- avoid blind character cuts in long fragments
- give Repo B cleaner transcript segments to store, display, or index

The internal artifact endpoint serializes these fields as snake_case `start_ms` and `end_ms`.
The `transcript.ready` Kafka v1 result remains correlation-only and contains no transcript rows.

## Intentionally not implemented

- timestamp synthesis for legacy/text-only rows
- speaker attribution
- chunk-level semantic search
- transcript editing
- word-level timestamps
