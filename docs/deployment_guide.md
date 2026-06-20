# Deployment Guide

This guide covers the processing-only branch of Repo A.

## Docker Compose services

- `backend`
- `worker`
- `consumer`
- `result-relay` (manual profile, one-shot)
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

2. Upload a video:

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

Kafka-originated worker completion now persists pending result-event intent in `processing_outbox_events`:

- `transcript.ready` v1 after transcript artifact rows and `ProcessingRequest.status="ready"` are persisted
- `asset.processing.failed` v1 after `ProcessingRequest.status="failed"` is persisted

When explicitly enabled and invoked, the one-shot relay publishes these rows to `asset.processing.result.v1`. Spring-side consumption is not implemented yet. FastAPI stores outbox rows as processing artifacts, not product truth.

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

The relay is disabled by default and is not scheduled. It claims due `pending` rows, marks them `publishing`, waits for Kafka acknowledgement, then marks them `published`. Publish failures return rows to `pending` with `next_attempt_at` until max attempts, after which rows become `failed`. The Kafka producer uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries. The runtime Kafka client is pinned to `kafka-python==2.3.1` for reproducible producer behavior.

Stuck `publishing` recovery after process interruption and DLQ/parking-topic handling are future work. Publication is at-least-once rather than end-to-end exactly-once because the relay can publish and then crash before marking the row `published`; future Spring consumers must be idempotent by result `eventId`.

This repository does not use Alembic yet. `Base.metadata.create_all` creates missing tables for new local databases, including `processing_outbox_events`. If an existing personal/local database cannot reflect schema changes automatically, recreating local data may be necessary.

## Runtime validation

```bash
python -m compileall backend/app
docker compose config
```

This repository intentionally avoids automated tests and a separate test image/runtime because the media and ML dependency stack is heavy for this personal project. Validate changes with runtime smoke checks, service logs, database inspection, and manual integration checks. This is a repository-specific trade-off, not a general backend recommendation.

## Branch-specific note

This branch is not meant to run a frontend or a search stack. If you are looking for product-facing behavior, use Repo B and Repo FE.

Spring-side consumption of completion/failure events is not implemented in this phase.
