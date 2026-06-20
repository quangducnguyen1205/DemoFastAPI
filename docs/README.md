# Repo A Branch Overview

Repo A on this branch is an internal processing service used by the current integrated product.

## Service boundary

- Repo B owns product-facing APIs, authorization, search behavior, and workspace/domain logic.
- Repo FE owns the product UI.
- Repo A owns internal processing intake, background processing, processing-state persistence, and transcript delivery.
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
8. When explicitly enabled and invoked, the one-shot result relay publishes due outbox rows to `asset.processing.result.v1`.

Offsets for valid events are committed after Celery handoff. Delivery is at-least-once, so duplicate events are handled by `eventId`. Result publishing uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries, but the outbox relay is still at-least-once and future Spring consumers must be idempotent by result `eventId`.

## Persistence boundary

Repo A still uses PostgreSQL because durable processing state matters for:

- upload-to-job linkage
- processing status
- transcript row retrieval after processing completes
- processing artifacts that can later be used when publishing completion/failure events back to Spring

Repo A does not act as the product system of record.

Spring consumption of completion/failure Kafka result events is intentionally not implemented yet. The current relay only publishes processing result events when explicitly enabled and manually invoked.

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
