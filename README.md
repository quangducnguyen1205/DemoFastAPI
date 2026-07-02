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
- persist and relay processing result events for Kafka-originated success/failure outcomes when explicitly enabled
- expose Kafka-originated transcript artifacts to Spring through an internal read-only retrieval endpoint
- expose one disabled-by-default internal grounded assistant answer endpoint for Spring-approved context
- return transcript results through the existing processing-side contract

## Not part of this branch

- no semantic or vector search runtime
- no FAISS or embedding pipeline
- no frontend/demo app
- no auth or user-management API
- no ownership/domain logic beyond legacy compatibility fields already accepted by the upload contract
- no automatic relay or listener behavior unless each side is explicitly enabled and started

## Active HTTP surface

- `GET /`
- `GET /health`
- `POST /videos/upload`
- `GET /videos/tasks/{task_id}`
- `GET /videos/{video_id}`
- `GET /videos/{video_id}/transcript`
- `GET /internal/processing-requests/{processingRequestId}/transcript-rows`
- `POST /internal/assistant/answer`

Kafka consumption is internal and does not add a public HTTP endpoint. The `/internal/.../transcript-rows` and `/internal/assistant/answer` endpoints are trusted deployment contracts for Spring service calls, not browser-facing product APIs.

## Runtime services

- `backend`: FastAPI API for upload/status/transcript endpoints
- `consumer`: Kafka consumer for `asset.processing.requested.v1`
- `result-relay`: optional relay process for `processing_outbox_events`
- `worker`: Celery worker for audio extraction, Whisper transcription, and transcript persistence
- `db`: PostgreSQL for durable processing state and transcript rows
- `redis`: Celery broker/result backend

Direct-upload media files are stored under `backend/media/` by default in this branch. Kafka-originated processing uses Spring-provided MinIO/S3 object references and the Celery worker downloads bytes internally when processing starts.

Kafka-originated worker completion writes `processing_outbox_events` rows for internal result contracts:

- `transcript.ready` v1
- `asset.processing.failed` v1

These rows are durable pending intent until an explicit relay publishes them, and their payloads deliberately exclude raw media bytes, transcript text, credentials, and stack traces.

When explicitly enabled and invoked, the manual one-shot result relay publishes pending outbox rows to the shared result topic:

```text
asset.processing.result.v1
```

Both success and failure use the same topic because they are result events for the same asset aggregate family; `eventType` distinguishes `transcript.ready` from `asset.processing.failed`. The manual relay is disabled by default, is not scheduled, and does not start Kafka. When enabled, the Kafka producer uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries. The runtime Kafka client is pinned to `kafka-python==2.3.1` for reproducible producer behavior.

The Project3 overlay can also run `result-relay` as a long-running automatic relay process. It has two safety gates: the service/command must be started explicitly, and `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED=true` must be set. Kafka publishing must still be explicitly enabled with `PROCESSING_RESULT_PUBLISHER_ENABLED=true`; otherwise the disabled publisher fails rather than pretending to publish. P3-D4 `[ĐÃ SMOKE THỰC TẾ]` verified this automatic relay in the fully automatic Spring/FastAPI path: one durable processing result outbox row was published to `asset.processing.result.v1`, no manual one-shot relay was invoked, and Spring applied the result through its automatic listener.

The internal assistant endpoint is disabled by default with `ASSISTANT_LLM_ENABLED=false`. When enabled later, it calls native host Ollama non-streaming and returns only `answer`, `citedSourceIds`, and `insufficientContext`. It does not retrieve context from PostgreSQL, Elasticsearch, MinIO, Kafka, Celery, or Spring APIs. P3-F2A adds code contracts only; it does not install Ollama, download `qwen3:1.7b`, start Docker/FastAPI/Spring, or perform an end-to-end answer smoke.

## Quickstart

Run the processing stack:

```bash
docker compose up --build backend worker consumer db redis
```

For Project3 cross-service runtime with Spring-owned Kafka and MinIO, start the Spring infrastructure first, then run DemoFastAPI with the integration overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.project3.yml up -d db redis backend consumer worker
```

The base Compose file remains standalone-compatible for direct upload and host-based local development. The overlay joins `backend`, `consumer`, `worker`, and the manual-profile `result-relay` service to the external Spring network `${SPRING_INFRA_NETWORK:-infra_default}`. Container-side integration defaults use `kafka:29092` for Kafka and `http://minio:9000` for services that read MinIO objects; `result-relay` only needs the Kafka/result-outbox side. With the overlay, `result-relay` runs the opt-in automatic relay entrypoint; without the overlay, the base service remains the manual one-shot relay. Use the overlay with an existing runtime image when possible; it does not add a new image, Dockerfile, build target, retry topic, DLQ, or production deployment claim.

Validate runtime wiring:

```bash
python -m compileall backend/app
docker compose config
docker compose -f docker-compose.yml -f docker-compose.project3.yml config
```

This repository intentionally does not maintain automated tests or a separate test image/runtime. The media and ML dependency stack is heavy enough that, for this personal project, validation uses runtime smoke checks, logs, database inspection, and manual integration checks instead. This is a repository-specific trade-off, not a general recommendation for backend services.

## Compatibility notes

- `POST /videos/upload` still returns `{task_id, status, video_id}`.
- `GET /videos/tasks/{task_id}` still mirrors Celery task state.
- `GET /videos/{video_id}/transcript` still returns ordered transcript rows by `segment_index`.
- `GET /internal/processing-requests/{processingRequestId}/transcript-rows` returns Kafka-originated processing artifact rows ordered by `segment_index`. It returns `404` for unknown processing requests and `409` when a request is failed, not ready, or ready without usable transcript artifacts.
- `owner_id` is still accepted on upload and returned on video reads for backward compatibility, but Repo A does not treat it as an authorization boundary.
- Kafka delivery is at-least-once. The consumer is idempotent by `eventId` using the local `processing_requests` table and commits valid offsets after successful Celery handoff.
- Result publication is also at-least-once. Producer idempotence does not make the outbox relay end-to-end exactly-once because a process can still publish and crash before marking the row `published`. Spring consumers must be idempotent by result `eventId`.
- The automatic result relay only relays due FastAPI processing result outbox rows for the existing `transcript.ready` and `asset.processing.failed` contracts. It does not scan arbitrary event tables and it does not recover rows stuck in `publishing`.
- FastAPI treats Kafka as transport and MinIO object keys as references; product metadata, authorization, workspace state, and final product status remain owned by Repo B.
- Kafka-originated transcript rows are processing artifacts that support later completion events back to Spring; they are not product truth.
- Result outbox rows are also processing artifacts. They record relay state and publication intent, not final product truth.
- Spring remains the owner of final product transcript snapshots after it retrieves and validates processing artifact rows.
- Production-grade service-to-service authentication or network policy for internal endpoints is not implemented in this phase.
- P3-F2A adds a disabled-by-default internal Ollama assistant adapter for Spring-approved context only. Ollama native-host runtime, model download, and end-to-end answer behavior remain future work.
- P3-D4 `[ĐÃ SMOKE THỰC TẾ]` verified the fully automatic path: Spring `kafka_request` plus automatic request relay, FastAPI consumer/Celery processing from MinIO, FastAPI automatic result relay, and Spring automatic result listener. Direct upload remained the default product mode and was not exercised; search/indexing stayed disabled.
- Stuck `publishing` recovery, default cutover away from `direct_upload`, generic all-event relay, production deployment hardening, retry topics, and DLQ handling are future work.
- This repo currently relies on SQLAlchemy `create_all` rather than Alembic. For personal/local schema changes, local DB data may need to be recreated if an existing database cannot be altered automatically.

## Documentation

- [docs/INDEX.md](./docs/INDEX.md)
- [docs/api_reference.md](./docs/api_reference.md)
- [docs/architecture.md](./docs/architecture.md)
- [docs/deployment_guide.md](./docs/deployment_guide.md)
- [docs/transcript_chunking.md](./docs/transcript_chunking.md)
