# AI Knowledge Workspace Processing Service

Repo A on this branch is an internal processing service for the integrated product.

- Repo B: product-facing backend and system of record
- Repo FE: product UI
- Repo A: internal Kafka consumer, upload compatibility path, process, poll status, fetch transcript

This branch intentionally removes product/demo/search responsibilities from Repo A.

## Active responsibility

- consume `asset.processing.requested.v1` from Kafka for Spring-owned assets
- accept media uploads from Repo B through the transitional direct-upload endpoint
- enqueue and run transcription processing
- persist processing state, direct-upload transcript rows, and Kafka-originated processing transcript artifacts
- persist and manually relay processing result events for Kafka-originated success/failure outcomes
- return transcript results through the existing processing-side contract

## Not part of this branch

- no semantic or vector search runtime
- no FAISS or embedding pipeline
- no frontend/demo app
- no auth or user-management API
- no ownership/domain logic beyond legacy compatibility fields already accepted by the upload contract
- no Spring-side consumption of transcript-ready or failed result events yet

## Active HTTP surface

- `GET /`
- `GET /health`
- `POST /videos/upload`
- `GET /videos/tasks/{task_id}`
- `GET /videos/{video_id}`
- `GET /videos/{video_id}/transcript`

Kafka consumption is internal and does not add a public HTTP endpoint.

## Runtime services

- `backend`: FastAPI API for upload/status/transcript endpoints
- `consumer`: Kafka consumer for `asset.processing.requested.v1`
- `result-relay`: optional one-shot relay for `processing_outbox_events`
- `worker`: Celery worker for audio extraction, Whisper transcription, and transcript persistence
- `db`: PostgreSQL for durable processing state and transcript rows
- `redis`: Celery broker/result backend

Direct-upload media files are stored under `backend/media/` by default in this branch. Kafka-originated processing uses Spring-provided MinIO/S3 object references and the Celery worker downloads bytes internally when processing starts.

Kafka-originated worker completion writes `processing_outbox_events` rows for internal result contracts:

- `transcript.ready` v1
- `asset.processing.failed` v1

These rows are durable pending intent until the explicit relay publishes them, and their payloads deliberately exclude raw media bytes, transcript text, credentials, and stack traces.

When explicitly enabled and invoked, the manual result relay publishes pending outbox rows to the shared result topic:

```text
asset.processing.result.v1
```

Both success and failure use the same topic because they are result events for the same asset aggregate family; `eventType` distinguishes `transcript.ready` from `asset.processing.failed`. The relay is disabled by default, is not scheduled, and does not start Kafka. When enabled, the Kafka producer uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries. The runtime Kafka client is pinned to `kafka-python==2.3.1` for reproducible producer behavior. Spring consumption of this topic is future work.

## Quickstart

Run the processing stack:

```bash
docker compose up --build backend worker consumer db redis
```

Validate runtime wiring:

```bash
python -m compileall backend/app
docker compose config
```

This repository intentionally does not maintain automated tests or a separate test image/runtime. The media and ML dependency stack is heavy enough that, for this personal project, validation uses runtime smoke checks, logs, database inspection, and manual integration checks instead. This is a repository-specific trade-off, not a general recommendation for backend services.

## Compatibility notes

- `POST /videos/upload` still returns `{task_id, status, video_id}`.
- `GET /videos/tasks/{task_id}` still mirrors Celery task state.
- `GET /videos/{video_id}/transcript` still returns ordered transcript rows by `segment_index`.
- `owner_id` is still accepted on upload and returned on video reads for backward compatibility, but Repo A does not treat it as an authorization boundary.
- Kafka delivery is at-least-once. The consumer is idempotent by `eventId` using the local `processing_requests` table and commits valid offsets after successful Celery handoff.
- Result publication is also at-least-once. Producer idempotence does not make the outbox relay end-to-end exactly-once because a process can still publish and crash before marking the row `published`. Future Spring consumers must be idempotent by result `eventId`.
- FastAPI treats Kafka as transport and MinIO object keys as references; product metadata, authorization, workspace state, and final product status remain owned by Repo B.
- Kafka-originated transcript rows are processing artifacts that support later completion events back to Spring; they are not product truth.
- Result outbox rows are also processing artifacts. They record relay state and publication intent, not final product truth.
- Stuck `publishing` recovery and DLQ handling are future work.
- This repo currently relies on SQLAlchemy `create_all` rather than Alembic. For personal/local schema changes, local DB data may need to be recreated if an existing database cannot be altered automatically.

## Documentation

- [docs/INDEX.md](./docs/INDEX.md)
- [docs/api_reference.md](./docs/api_reference.md)
- [docs/architecture.md](./docs/architecture.md)
- [docs/deployment_guide.md](./docs/deployment_guide.md)
- [docs/transcript_chunking.md](./docs/transcript_chunking.md)
