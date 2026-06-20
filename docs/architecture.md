# Processing-Service Architecture

## Role in the integrated system

Repo A is the internal processing service.

- Repo B publishes `asset.processing.requested.v1` and may still call Repo A for transitional upload, task polling, and transcript retrieval.
- Repo B remains the product-facing backend and search owner.
- Repo FE remains the UI.

## Runtime components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| FastAPI API | `backend/app/main.py` | Exposes the processing endpoints and health/docs surface. |
| Videos router | `backend/app/routers/videos.py` | Upload, task polling, single-video status lookup, and transcript retrieval. |
| Kafka consumer | `backend/app/consumers/asset_processing_consumer.py` | Consumes `asset.processing.requested.v1`, validates envelopes, applies idempotency, and hands accepted work to Celery. |
| Celery app | `backend/app/core/celery_app.py` | Queue orchestration for background processing. |
| Worker task | `backend/app/tasks/video_tasks.py` | Extract audio, transcribe, chunk transcript text, persist direct-upload transcripts, and update processing state. |
| Object storage | `backend/app/services/object_storage.py` | S3-compatible MinIO access used by workers to download Spring-owned media objects. |
| Processing helpers | `backend/app/services/video_processing.py` | ffmpeg extraction, Whisper access, transcript chunking, and transcript persistence. |
| Persistence | `backend/app/models/video.py`, `backend/app/models/transcript.py`, `backend/app/models/processing_request.py` | Durable direct-upload processing state, transcript rows, Kafka idempotency records, and Kafka-originated transcript artifacts. |

## End-to-end flow

### Transitional direct-upload flow

1. `POST /videos/upload` stores the file under `MEDIA_ROOT/videos/`.
2. The API inserts a `videos` row and sets `status="processing"`.
3. The API enqueues `process_video_task(video_id, abs_video_path)`.
4. The worker:
   - extracts mono WAV audio via ffmpeg
   - transcribes audio with Whisper
   - chunks transcript text
   - persists transcript rows in PostgreSQL
   - updates `videos.status` to `ready` or `failed`
5. Repo B polls `GET /videos/tasks/{task_id}` and can fetch `GET /videos/{video_id}` or `GET /videos/{video_id}/transcript`.

This path remains for compatibility while Project3 moves ingestion ownership into Spring Boot and MinIO.

### Kafka-originated asset flow

1. Spring Boot writes product state in PostgreSQL and stores raw bytes in MinIO.
2. Spring Boot publishes `asset.processing.requested.v1` through its outbox relay.
3. The FastAPI consumer group `fastapi-processing-v1` reads the event and validates:
   - `eventType == "asset.processing.requested"`
   - `eventVersion == 1`
   - required envelope metadata
   - required payload object reference fields
4. The consumer records `eventId` in `processing_requests` before handoff.
5. The consumer enqueues `process_asset_object` with object-reference metadata only: bucket, object key, asset id, content type, and original filename.
6. The consumer commits the Kafka offset after successful Celery handoff. Malformed or unsupported messages are logged and committed to avoid blocking the partition until a DLQ exists.
7. The Celery worker downloads bytes from MinIO internally, transcribes, chunks, persists transcript artifact rows, and updates the internal processing request status.

Delivery is at-least-once. Duplicate Kafka deliveries are expected and are suppressed by the unique `eventId` record in `processing_requests`. The Celery task also checks request state before processing to avoid repeating completed or already-running work.

## Persistence boundary

Repo A intentionally keeps only processing-oriented state:

- `videos`
  - upload metadata
  - storage path
  - durable processing status
  - optional legacy `owner_id` passthrough
- `transcripts`
  - ordered transcript rows by `segment_index`
- `processing_requests`
  - Kafka `eventId` idempotency key
  - object reference metadata needed for worker handoff
  - Celery task id and internal processing status
- `processing_request_transcripts`
  - transcript segment artifacts tied to `processing_requests.event_id`
  - segment index and text
  - nullable timing fields for future pipeline output

Repo A does not own product auth, user identity, asset metadata, search indexes, or workspace/business logic in this branch. Kafka is transport, not a transfer of product-state ownership. Completion/failure events back to Spring are not implemented yet.

Kafka-originated transcript rows are processing artifacts. They support later completion/failure event publishing back to Spring but do not make FastAPI the product source of truth.

## Removed from active runtime

- semantic/vector search
- FAISS index files and mapping management
- embedding generation
- auth and user CRUD
- frontend/demo app
