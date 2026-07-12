# AI Knowledge Workspace Processing Service

Repo A on this branch is an internal processing service for the integrated product.

- Repo B: product-facing backend and system of record
- Repo FE: product UI
- Repo A: internal Kafka consumer, upload compatibility path, process, poll status, fetch transcript

This branch intentionally removes product/demo/search responsibilities from Repo A.

## Active responsibility

- consume `asset.processing.requested.v1` from Kafka for Spring-owned assets
- retain the deprecated direct-upload endpoint for Spring rollback compatibility and generic standalone use
- enqueue and run transcription processing
- persist processing state, direct-upload transcript rows, and Kafka-originated processing transcript artifacts
- persist and relay processing result events for Kafka-originated success/failure outcomes when explicitly enabled
- expose Kafka-originated transcript artifacts to Spring through an internal read-only retrieval endpoint
- expose one internal grounded assistant answer endpoint for Spring-approved context; generic defaults stay disabled while the integrated Project3 topology enables the validated local runtime
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
- `POST /videos/upload` (deprecated in OpenAPI, still functional)
- `GET /videos/tasks/{task_id}`
- `GET /videos/{video_id}`
- `GET /videos/{video_id}/transcript`
- `GET /internal/processing-requests/{processingRequestId}/transcript-rows`
- `POST /internal/assistant/answer`

Kafka consumption is internal and does not add a public HTTP endpoint. The `/internal/.../transcript-rows` and `/internal/assistant/answer` endpoints are trusted deployment contracts for Spring service calls, not browser-facing product APIs.

## Runtime services

- `backend`: FastAPI API for upload/status/transcript endpoints
- `consumer`: Kafka consumer for `asset.processing.requested.v1`
- `result-relay`: automatic relay in the Project3 topology and retained one-shot/manual relay in base Compose
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

The same Project3 relay process now performs bounded failed-outbox reconciliation before each normal relay iteration. Only rows whose terminal publication failure was classified from typed exception data as `transient` are eligible, after a 60-second cooldown and for at most three recovery cycles. Requeue is an atomic compare-and-set back to the existing `pending` path, resets the five normal publication attempts, and preserves event identity and payload. Serialization/event-construction failures are `permanent`; unrecognized and historical failures are `unknown`; rows that exhaust the recovery budget become `recovery_exhausted`. Those terminal categories are never automatically replayed. Generic and one-shot operation keep reconciliation disabled unless explicitly configured.

Generic source and base-Compose assistant defaults remain disabled. The Project3 overlay enables the runtime-proven local settings (`qwen3:4b`, 60-second provider timeout, `num_predict=256`) and keeps non-streaming structured generation. Controlled P3-S2 validation proved Spring-selected context, FastAPI Pydantic parsing, request-local citation aliases, alias-to-canonical mapping, Spring canonical validation, and frontend citation navigation. FastAPI still performs no independent retrieval and exposes no provider controls to the browser.

## Quickstart

Run the processing stack:

```bash
docker compose up --build backend worker consumer db redis
```

For the coherent Project3 runtime with Spring-owned Kafka and MinIO, start Spring infrastructure first, ensure the existing FastAPI image is available, then run:

```bash
make project3-up
```

`make project3-up` uses both Compose files and explicitly includes `db`, `redis`, `backend`, `worker`, `consumer`, and automatic `result-relay` without building or pulling. The relay receives both required safety gates. Base Compose and `make up` remain standalone-compatible; the one-shot relay and direct-upload endpoints are not removed.

The controlled local observation campaign supports documented deprecation of the Spring direct-processing compatibility path, but it does not claim production-scale stability. New Project3 integrations must use the Kafka consumer path.

Validate runtime wiring:

```bash
python -m compileall backend/app
docker compose config
docker compose -f docker-compose.yml -f docker-compose.project3.yml config
```

Focused Python unit tests cover assistant structured generation plus processing-event validation, idempotent enqueue behavior, terminal result outbox intent, relay safety gates, and configuration overrides. They mock infrastructure boundaries and do not call Kafka, Celery workers, MinIO, FastAPI HTTP, or Ollama.

## Direct processing deprecation

`POST /videos/upload` is deprecated in FastAPI/OpenAPI metadata but remains fully functional. Each invocation emits one safe warning stating that the endpoint is retained for rollback compatibility and that the Project3 Kafka consumer is the replacement. The warning contains no file name, title, owner, task, account, credential, or payload data.

No removal date is assigned. The endpoint continues to preserve the same path, multipart request, response fields, status behavior, file persistence, database writes, and Celery enqueue behavior. It remains available for the Spring `compatibility` profile and generic standalone FastAPI use outside Project3 integration. Removal requires a completed deprecation window, caller and standalone-use audits, replacement observation evidence, a rollback plan, and a separate removal decision.

Explicit indexing recovery, manual/one-shot relays, exact-ID recovery, and legacy session authentication are outside this deprecation scope.

## Compatibility notes

- Deprecated `POST /videos/upload` still returns `{task_id, status, video_id}` and remains callable by Spring rollback mode.
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
- Historical failed result-outbox rows are upgraded as `unknown` and remain terminal for manual review; the schema upgrader never infers transient safety from an old free-form error.
- Spring remains the owner of final product transcript snapshots after it retrieves and validates processing artifact rows.
- Production-grade service-to-service authentication or network policy for internal endpoints is not implemented in this phase.
- Generic mode keeps the internal Ollama adapter disabled. The Project3 overlay enables only the validated local provider values; runtime installation and model availability remain operator prerequisites.
- P3-D4 `[ĐÃ SMOKE THỰC TẾ]` verified the fully automatic path: Spring `kafka_request` plus automatic request relay, FastAPI consumer/Celery processing from MinIO, FastAPI automatic result relay, and Spring automatic result listener. Direct upload remained the default product mode and was not exercised; search/indexing stayed disabled.
- Stuck `publishing` recovery, generic all-event relay, production deployment hardening, retry topics, and a full Kafka DLQ remain future work. Direct upload and manual relay remain executable rollback paths.
- This repo still uses SQLAlchemy metadata rather than Alembic. Startup creates missing tables and applies a narrow idempotent processing-outbox column/index upgrade for existing local databases; historical terminal failures are classified `unknown` instead of being replayed.

## Documentation

- [docs/INDEX.md](./docs/INDEX.md)
- [docs/api_reference.md](./docs/api_reference.md)
- [docs/architecture.md](./docs/architecture.md)
- [docs/deployment_guide.md](./docs/deployment_guide.md)
- [docs/transcript_chunking.md](./docs/transcript_chunking.md)
