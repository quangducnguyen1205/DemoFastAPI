# Repo A Branch Overview

Repo A on this branch is an internal processing service used by the current integrated product.

## Service boundary

- Repo B owns product-facing APIs, authorization, search behavior, and workspace/domain logic.
- Repo FE owns the product UI.
- Repo A owns internal processing intake, background processing, processing-state persistence, and transcript delivery.
- Repo A owns one internal grounded-answer adapter endpoint for trusted Spring calls, but Spring still supplies all assistant context and validates final citations.
- Kafka and MinIO are integration boundaries: Repo A consumes object references from Kafka and reads bytes from MinIO, but Repo B remains the product system of record.

## Processing flow

### Transitional direct-upload flow

1. Repo B uploads media through `POST /videos/upload`.
2. Repo A stores the file locally and creates a `videos` row with `status="processing"`.
3. Repo A enqueues `process_video_task(video_id, abs_video_path)` through Celery.
4. The worker extracts audio, runs Whisper, chunks transcript text, persists `transcripts` rows, and updates `videos.status`.
5. Repo B polls `GET /videos/tasks/{task_id}` and can read `GET /videos/{video_id}` or `GET /videos/{video_id}/transcript`.

### Kafka object-reference flow

1. Repo B/Spring stores raw bytes in MinIO and publishes `asset.processing.requested.v1`.
2. Repo A's `consumer` service validates the event envelope and payload.
3. Repo A writes a `processing_requests` row keyed by `eventId` for idempotency.
4. Repo A hands object-reference metadata to Celery; raw media bytes are not placed in Kafka or Celery payloads.
5. The worker downloads the object from MinIO internally before transcription.
6. The worker stores transcript segment artifacts tied to the `processing_requests.event_id`.
7. The worker writes a pending processing result outbox row for `transcript.ready` or `asset.processing.failed`.
8. When explicitly enabled and invoked, the result relay publishes due outbox rows to `asset.processing.result.v1`.
9. Spring can retrieve ready transcript artifact rows through `GET /internal/processing-requests/{processingRequestId}/transcript-rows` before persisting its product-owned transcript snapshot.

Offsets for valid events are committed after Celery handoff. Delivery is at-least-once, so duplicate events are handled by `eventId`. Result publishing uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries, but the outbox relay is still at-least-once and Spring consumers must be idempotent by result `eventId`.

## Persistence boundary

Repo A still uses PostgreSQL because durable processing state matters for:

- upload-to-job linkage
- processing status
- transcript row retrieval after processing completes
- processing artifacts that can later be used when publishing completion/failure events back to Spring

Repo A does not act as the product system of record.

Spring has a disabled-by-default result listener for completion/failure events. Repo A's base relay remains a manual one-shot command, and the Project3 overlay can run a dedicated long-running automatic relay process only when that service is explicitly started and `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED=true`. P3-D4 `[ĐÃ SMOKE THỰC TẾ]` verified the automatic relay in the fully automatic runtime path: Spring automatic request relay, Repo A consumer/Celery processing from MinIO, Repo A automatic result relay, and Spring automatic result listener completed one upload without manual request or result relay commands.

## Internal assistant adapter

P3-F2A adds `POST /internal/assistant/answer` for Spring-owned grounded answer orchestration. The request contains a question and bounded source entries supplied by Spring; Repo A does not call PostgreSQL, Elasticsearch, MinIO, Kafka, Celery, or Spring to retrieve assistant context. The adapter path is disabled by default with `ASSISTANT_LLM_ENABLED=false`. When enabled later, it calls native host Ollama non-streaming through `/api/generate`, requests JSON output, and returns only `answer`, `citedSourceIds`, and `insufficientContext`.

Ollama is intended to run natively on the user's macOS host with `qwen3:1.7b` in a later runtime phase. This code change does not install Ollama, download a model, start Docker, run FastAPI, or perform an end-to-end answer smoke.

## Legacy compatibility

- `owner_id` is still accepted on upload and returned on video reads if Repo B already sends it.
- Direct upload remains transitional while Kafka object-reference processing is introduced.
- Search endpoints, FAISS state, user routes, and frontend code are removed from the active branch.

## Validation policy

This repository intentionally does not maintain automated tests or a separate test image/runtime because its heavy media and ML dependency stack makes that impractical for this personal project. Validation uses runtime smoke checks, logs, database inspection, and manual integration checks. This is a repository-specific trade-off, not a general recommendation for backend services.

## Current docs

- [api_reference.md](./api_reference.md)
- [architecture.md](./architecture.md)
- [deployment_guide.md](./deployment_guide.md)
- [transcript_chunking.md](./transcript_chunking.md)
