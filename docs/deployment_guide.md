# Deployment Guide

This guide covers the processing-only branch of Repo A.

## Docker Compose services

- `backend`
- `worker`
- `consumer`
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
- `KAFKA_CONSUMER_GROUP` (default: `fastapi-processing-v1`)
- `KAFKA_RECONNECT_BACKOFF_SECONDS` (default: `5`)
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

## Runtime validation

```bash
python -m compileall backend/app
docker compose config
```

This repository intentionally avoids automated tests and a separate test image/runtime because the media and ML dependency stack is heavy for this personal project. Validate changes with runtime smoke checks, service logs, database inspection, and manual integration checks. This is a repository-specific trade-off, not a general backend recommendation.

## Branch-specific note

This branch is not meant to run a frontend or a search stack. If you are looking for product-facing behavior, use Repo B and Repo FE.

Completion/failure events from FastAPI back to Spring are not implemented in this phase.
