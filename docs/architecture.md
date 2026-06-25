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
| Internal processing router | `backend/app/routers/internal_processing.py` | Read-only retrieval of Kafka-originated transcript artifact rows for trusted Spring service calls. |
| Kafka consumer | `backend/app/consumers/asset_processing_consumer.py` | Consumes `asset.processing.requested.v1`, validates envelopes, applies idempotency, and hands accepted work to Celery. |
| Celery app | `backend/app/core/celery_app.py` | Queue orchestration for background processing. |
| Worker task | `backend/app/tasks/video_tasks.py` | Extract audio, transcribe, chunk transcript text, persist direct-upload transcripts, update processing state, and persist result outbox intent for Kafka-originated work. |
| Object storage | `backend/app/services/object_storage.py` | S3-compatible MinIO access used by workers to download Spring-owned media objects. |
| Processing outbox | `backend/app/services/processing_outbox.py` | Builds internal result-event contracts and inserts pending outbox rows in the worker transaction. |
| Result publisher | `backend/app/services/processing_outbox_publisher.py` | Builds result envelopes and publishes to Kafka when explicitly enabled. |
| Result relay | `backend/app/services/processing_outbox_relay.py`, `backend/app/relays/processing_outbox_relay.py`, `backend/app/relays/processing_outbox_auto_relay.py` | Manual one-shot relay plus opt-in automatic relay process. Both reuse the same durable claim/publish/retry state machine. |
| Processing helpers | `backend/app/services/video_processing.py` | ffmpeg extraction, Whisper access, transcript chunking, and transcript persistence. |
| Persistence | `backend/app/models/video.py`, `backend/app/models/transcript.py`, `backend/app/models/processing_request.py` | Durable direct-upload processing state, transcript rows, Kafka idempotency records, Kafka-originated transcript artifacts, and pending result outbox rows. |

## Compose topology

The base `docker-compose.yml` remains the standalone/local processing topology. It keeps DemoFastAPI `db` and `redis` on the normal Compose network and preserves host-oriented defaults for local direct-upload behavior.

Project3 cross-service runtime uses the additive overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.project3.yml ...
```

The overlay attaches only `backend`, `consumer`, `worker`, and the manual-profile `result-relay` service to the external Spring infrastructure network `${SPRING_INFRA_NETWORK:-infra_default}`. It leaves DemoFastAPI `db` and `redis` on the local DemoFastAPI network. Container-side integration defaults become `KAFKA_BOOTSTRAP_SERVERS=kafka:29092` and `OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000` for services that need object storage, matching Spring Compose service names. `result-relay` only receives the Kafka/result-outbox configuration it needs. With the overlay, `result-relay` runs the opt-in automatic relay entrypoint; without the overlay, the base Compose service remains the manual one-shot relay. This removes the previous runtime-only network workaround without making the base Compose file depend on Spring infrastructure.

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
7. The Celery worker downloads bytes from MinIO internally, transcribes, chunks, persists transcript artifact rows, updates the internal processing request status, and inserts a pending result outbox row in the same database transaction.

Delivery is at-least-once. Duplicate Kafka deliveries are expected and are suppressed by the unique `eventId` record in `processing_requests`. The Celery task also checks request state before processing to avoid repeating completed or already-running work.

### Kafka-originated result intent

When Kafka-originated processing reaches a terminal state, Repo A persists a `processing_outbox_events` row:

- success: `transcript.ready` version 1
- failure: `asset.processing.failed` version 1

These are internal outbox contracts. Repo A can publish them to Kafka only when a result relay process and Kafka publisher are explicitly enabled. Spring has both a manual result-handler foundation and a disabled-by-default automatic result listener; Spring remains responsible for idempotent product-state application.

Common envelope fields are represented by outbox columns:

- `id` as `eventId`
- `event_type`
- `event_version`
- `aggregate_type = "ASSET"`
- `aggregate_id = assetId`
- `event_key = assetId`
- `causation_event_id = original asset.processing.requested eventId`
- `occurred_at`
- `payload`

The `transcript.ready` payload contains only `assetId`, `processingRequestId`, `status`, `segmentCount`, and `completedAt`. The `asset.processing.failed` payload contains only `assetId`, `processingRequestId`, `status`, `errorCode`, a bounded safe `errorMessage`, and `completedAt`.

Result payloads do not include raw media bytes, transcript text, MinIO credentials, stack traces, or product authorization data. Transcript rows remain local processing artifacts referenced by `processingRequestId`.

### Internal transcript artifact retrieval

When Spring handles `transcript.ready`, it retrieves transcript content through:

```text
GET /internal/processing-requests/{processingRequestId}/transcript-rows
```

The `processingRequestId` is the original Spring request event ID stored as `ProcessingRequest.event_id`. The endpoint returns the processing artifact rows in ascending `segment_index` order using Spring's existing transcript-row JSON shape: `id`, `video_id`, `segment_index`, `text`, and `created_at`. For Kafka-originated artifacts, `video_id` carries the processing request/event ID because there is no direct-upload `videos.id` row.

This endpoint is read-only. It does not change `ProcessingRequest`, transcript rows, Celery state, Kafka, or outbox rows. It returns `404` for unknown requests and `409` when a request is failed, not ready, or ready without usable artifact rows.

This is an internal deployment contract, not a public product API. Production-grade service-to-service authentication and network policy are not implemented in this phase, and Spring remains the owner of final product transcript snapshots after retrieval and validation.

### Result outbox relay

The shared result topic is:

```text
asset.processing.result.v1
```

Both `transcript.ready` and `asset.processing.failed` are result events for the same asset aggregate family, so they share one topic. The envelope `eventType` distinguishes success from failure. Kafka message key is `event_key`, which is the asset id, to keep same-asset result events on the same Kafka key.

The relay state machine is deliberately small:

```text
pending -> publishing -> published
pending -> publishing -> pending
pending -> publishing -> failed
```

Rules:

- the relay only selects `pending` rows whose `next_attempt_at` is null or due;
- it conditionally claims each row by changing `pending` to `publishing`;
- it waits for Kafka producer acknowledgement with a bounded timeout;
- on success it sets `published`, `published_at`, clears retry fields, and commits;
- on failure it increments `attempt_count`, stores a bounded safe `last_error`, and either returns to `pending` with `next_attempt_at` or transitions to `failed` after max attempts.

The manual relay is disabled by default and is not scheduled. It can be invoked once through `python -m app.relays.processing_outbox_relay` or the base Compose `result-relay` profile with `PROCESSING_OUTBOX_RELAY_ENABLED=true`.

The Project3 overlay can run `result-relay` as a long-running automatic relay process with `python -m app.relays.processing_outbox_auto_relay`. That process has two safety gates: the command/service must be started explicitly, and `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED=true` must be set. It runs bounded iterations using `PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE`, sleeps for `PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS`, and logs aggregate iteration counts only when rows were claimed, retried, or terminally failed. It remains stateless between iterations except for PostgreSQL state.

Both relay modes use the same publisher boundary. There is no logging publisher that pretends to publish; if Kafka publishing is disabled, the publisher fails explicitly.

The result Kafka producer uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries while keeping the existing bounded acknowledgement timeout.

The automatic relay does not live inside `backend`, `consumer`, or `worker`, and it does not scan arbitrary event tables. Result payloads stay compact and do not contain transcript text, raw media bytes, object storage credentials, tokens, stack traces, or product ownership data.

P3-D4 `[ĐÃ SMOKE THỰC TẾ]` verified this process as part of the fully automatic runtime path: the overlay `result-relay` service published one selected durable result outbox row after the consumer and Celery worker processed a Spring-owned MinIO object, and Spring's automatic result listener applied the result. No base one-shot result relay was invoked. Direct upload remained the product default and was not exercised; indexing/search stayed disabled.

Stuck `publishing` recovery after process interruption is not implemented yet. DLQ and parking-topic handling are also future work. Publication is at-least-once, not end-to-end exactly-once, because a relay process can publish and then crash before marking the outbox row `published`. Spring consumers must be idempotent by result `eventId`.

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
- `processing_outbox_events`
  - result-event relay state for Kafka-originated processing completion/failure
  - unique `(causation_event_id, event_type)` to prevent duplicate outbox intent from duplicate task execution
  - publish bookkeeping fields for the result relay: `status`, `attempt_count`, `next_attempt_at`, `last_error`, and `published_at`

Repo A does not own product auth, user identity, asset metadata, search indexes, or workspace/business logic in this branch. Kafka is transport, not a transfer of product-state ownership.

Kafka-originated transcript and outbox rows are processing artifacts. They support later completion/failure event publishing back to Spring but do not make FastAPI the product source of truth.

This repo currently uses SQLAlchemy `create_all` instead of Alembic. New local/personal databases get the new tables automatically; existing local DB data may need to be recreated if schema changes cannot be applied automatically.

## Removed from active runtime

- semantic/vector search
- FAISS index files and mapping management
- embedding generation
- auth and user CRUD
- frontend/demo app
