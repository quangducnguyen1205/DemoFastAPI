# Deployment Guide

This guide covers the processing-only branch of Repo A.

## Docker Compose services

- `backend`
- `worker`
- `consumer`
- `result-relay` (one-shot/manual in base Compose; automatic and active in the Project3 overlay)
- `db`
- `redis`

## Start the processing stack

```bash
docker compose up --build backend worker consumer db redis
```

## Important environment values

- `DATABASE_URL`
- `MEDIA_ROOT`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CELERY_WORKER_PREFETCH_MULTIPLIER`
- `KAFKA_BOOTSTRAP_SERVERS`
- `KAFKA_ASSET_PROCESSING_TOPIC` (default: `asset.processing.requested.v1`)
- `KAFKA_PROCESSING_RESULT_TOPIC` (default: `asset.processing.result.v1`)
- `KAFKA_CONSUMER_GROUP` (default: `fastapi-processing-v1`)
- `KAFKA_RECONNECT_BACKOFF_SECONDS` (default: `5`)
- `KAFKA_SEND_TIMEOUT_SECONDS` (default: `10`)
- `PROCESSING_RESULT_PUBLISHER_ENABLED` (default: `false`)
- `PROCESSING_OUTBOX_RELAY_ENABLED` (default: `false`)
- `PROCESSING_OUTBOX_RELAY_BATCH_SIZE` (default: `10`)
- `PROCESSING_OUTBOX_RELAY_MAX_ATTEMPTS` (default: `5`)
- `PROCESSING_OUTBOX_RELAY_RETRY_DELAY_SECONDS` (default: `60`)
- `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED` (default: `false`)
- `PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS` (default: `10`)
- `PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE` (default: `10`)
- `OBJECT_STORAGE_ENDPOINT_URL`
- `OBJECT_STORAGE_ACCESS_KEY_ID`
- `OBJECT_STORAGE_SECRET_ACCESS_KEY`
- `OBJECT_STORAGE_REGION`

Current Compose defaults align media storage at `/backend/media` inside the backend and worker containers.

This compose file does not start Kafka or MinIO. Those are expected to be available from the product/Spring infrastructure and are referenced through explicit environment variables. The `consumer` process is separate from the FastAPI API process so Kafka polling does not live inside request handling.

## Basic verification

1. Check health:

```bash
curl http://localhost:8000/health
```

2. For standalone compatibility only, invoke the deprecated but functional direct endpoint:

```bash
curl -X POST http://localhost:8000/videos/upload \
  -F "file=@sample.mp4" \
  -F "title=Sample Lecture"
```

3. Poll task status:

```bash
curl http://localhost:8000/videos/tasks/<task_id>
```

4. Fetch transcript:

```bash
curl http://localhost:8000/videos/<video_id>/transcript
```

5. Start the Kafka consumer when a broker is available:

```bash
docker compose up --build consumer
```

The consumer commits valid offsets only after successful Celery handoff. Invalid or unsupported messages are logged and committed to avoid blocking the partition because this phase has no DLQ. Processing remains at-least-once and idempotent by `eventId`.

## Project3 cross-compose integration

Base `docker-compose.yml` remains usable by itself for standalone/direct-upload development. For the coherent Project3 async path, ensure the existing image is available and use:

```bash
make project3-up
```

New Project3 integrations must use this Kafka consumer topology. The direct endpoint remains available for rollback and generic standalone use during its deprecation period; no removal date is assigned.

Migration from the old normal local flow is:

```text
old: compatibility/direct upload -> TRANSCRIPT_READY -> explicit Index transcript
new: make project3-up + Spring make run -> upload -> automatic processing -> automatic indexing -> SEARCHABLE
```

Explicit indexing recovery, one-shot/manual relays, and exact-ID recovery remain supported and are not deprecated.

Expected local startup order:

1. Start Spring infrastructure first, including Kafka, MinIO, topic bootstrap, and bucket bootstrap.
2. Start DemoFastAPI with both Compose files.
3. Start the Spring application or run the manual smoke command.

The target renders both Compose files, starts `db`, `redis`, `backend`, `worker`, `consumer`, and automatic `result-relay`, and passes `--no-build`. The overlay expects the Spring Compose network to exist as `${SPRING_INFRA_NETWORK:-infra_default}`. DemoFastAPI `db` and `redis` stay on the normal local network.

Container-side integration defaults in the overlay are:

```text
KAFKA_BOOTSTRAP_SERVERS=${PROJECT3_KAFKA_BOOTSTRAP_SERVERS:-kafka:29092}
OBJECT_STORAGE_ENDPOINT_URL=${PROJECT3_OBJECT_STORAGE_ENDPOINT_URL:-http://minio:9000}
```

These names match the Spring infrastructure services on `infra_default`. The overlay removes the previous temporary workaround of manually connecting running FastAPI containers to `infra_default` and using mixed host/container addresses. It does not add a new image, build target, automatic listener, retry topic, DLQ, or production deployment claim. Use `--pull never` during smoke runs when reusing an existing local runtime image.

`result-relay` joins the Spring network for Kafka access, but it does not receive MinIO/object-storage configuration because publishing result outbox rows does not read media objects.

Kafka-originated worker completion now persists pending result-event intent in `processing_outbox_events`:

- `transcript.ready` v1 after transcript artifact rows and `ProcessingRequest.status="ready"` are persisted
- `asset.processing.failed` v1 after `ProcessingRequest.status="failed"` is persisted

When explicitly enabled and invoked, the relay publishes these rows to `asset.processing.result.v1`. FastAPI stores outbox rows as processing artifacts, not product truth.

Spring can retrieve Kafka-originated transcript artifacts through the internal read-only endpoint:

```text
GET /internal/processing-requests/{processingRequestId}/transcript-rows
```

The endpoint returns ordered rows with `id`, `video_id`, `segment_index`, `text`, and `created_at`, matching Spring's existing FastAPI transcript-row DTO. It returns `404` for unknown processing requests and `409` when a request is failed, not ready, or ready without usable transcript artifacts. It is an internal deployment contract only; production-grade service-to-service authentication and network policy are not implemented in this phase.

Run the relay once from local Python:

```bash
PROCESSING_OUTBOX_RELAY_ENABLED=true \
PROCESSING_RESULT_PUBLISHER_ENABLED=true \
PYTHONPATH=backend \
python -m app.relays.processing_outbox_relay
```

Or through Compose with the manual profile:

```bash
PROCESSING_OUTBOX_RELAY_ENABLED=true \
PROCESSING_RESULT_PUBLISHER_ENABLED=true \
docker compose --profile manual run --rm result-relay
```

The normal Project3 target starts the automatic relay with both safety gates enabled:

```bash
make project3-up
```

The automatic relay remains a dedicated long-running process, not behavior inside `backend`, `consumer`, or `worker`. The Project3 overlay coherently sets `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED=true` and `PROCESSING_RESULT_PUBLISHER_ENABLED=true`; the process still validates both gates at startup. Base Compose preserves the disabled one-shot/manual behavior.

The base one-shot relay uses `PROCESSING_OUTBOX_RELAY_ENABLED` and `PROCESSING_OUTBOX_RELAY_BATCH_SIZE`. The automatic relay uses `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED`, `PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS`, and `PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE` while preserving the same retry/max-attempt settings. Invalid auto interval or batch-size values fail at startup.

Both relay modes claim due `pending` rows, mark them `publishing`, wait for Kafka acknowledgement, then mark them `published`. Publish failures return rows to `pending` with `next_attempt_at` until max attempts, after which rows become `failed`. Each row is processed independently, and the database claim transaction is committed before waiting for Kafka. The Kafka producer uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries. The runtime Kafka client is pinned to `kafka-python==2.3.1` for reproducible producer behavior.

Stuck `publishing` recovery after process interruption and DLQ/parking-topic handling are future work. Publication is at-least-once rather than end-to-end exactly-once because the relay can publish and then crash before marking the row `published`; Spring consumers must be idempotent by result `eventId`.

The automatic relay publishes only due FastAPI processing-result outbox rows through the existing supported contracts: `transcript.ready` and `asset.processing.failed`. It is not a generic event relay and does not place transcript text, media bytes, object storage credentials, tokens, stack traces, or product ownership data in result payloads. P3-D4 `[ĐÃ SMOKE THỰC TẾ]` verified the automatic relay with the Project3 overlay in the fully automatic Spring/FastAPI path: Spring automatic request relay, FastAPI consumer/Celery, FastAPI automatic result relay, and Spring automatic result listener completed one upload without manual request/result controls. Direct upload remained the default product mode and was not exercised; indexing/search stayed disabled.

This repository does not use Alembic yet. `Base.metadata.create_all` creates missing tables for new local databases, including `processing_outbox_events`. If an existing personal/local database cannot reflect schema changes automatically, recreating local data may be necessary.

## Runtime validation

```bash
python -m compileall backend/app
docker compose config
```

This repository intentionally avoids automated tests and a separate test image/runtime because the media and ML dependency stack is heavy for this personal project. Validate changes with runtime smoke checks, service logs, database inspection, and manual integration checks. This is a repository-specific trade-off, not a general backend recommendation.

## Branch-specific note

This branch is not meant to run a frontend or a search stack. If you are looking for product-facing behavior, use Repo B and Repo FE.

Automatic Spring Kafka listener consumption exists in the product repository but remains disabled by default. Enable it only as part of a controlled local integration run.
