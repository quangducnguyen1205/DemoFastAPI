# FastAPI Processing Pipeline Boundaries

This note records the P3-S5.B3 processing-service ownership boundaries. Spring remains the
product system of record; FastAPI owns only processing commands, scratch processing state,
transcript artifacts, and durable result-delivery intent.

## Pre-refactor responsibility map

| Previous owner | Classification | Caller and I/O | Transaction/retry ownership | Target owner |
|---|---|---|---|---|
| `events.asset_processing` Pydantic models/parser | REQUEST_TRANSPORT_ADAPTER | Kafka consumer; JSON decode/validation only | Rejects malformed/type/version mismatches; no session | Kafka ingestion adapter mapping to `ProcessingRequestCommand` |
| `consumers.asset_processing_consumer` | REQUEST_TRANSPORT_ADAPTER / RUNTIME_BOOTSTRAP | Kafka client, parser, database session, acceptance service | Commits valid/rejected offsets after handoff; leaves offset uncommitted on handoff/commit failure; reconnect loop | Thin Kafka adapter plus consumer bootstrap |
| `services.processing_requests` | PROCESSING_APPLICATION mixed with TASK_DISPATCH_ADAPTER | Consumer; SQLAlchemy and direct Celery task import | Commits idempotency row before dispatch and enqueued state after dispatch | Dispatch application service, request repository, Celery dispatcher |
| `tasks.video_tasks.process_asset_object_task` | PROCESSING_APPLICATION mixed with MEDIA_OR_PROVIDER_ADAPTER, ARTIFACT_ADAPTER, RESULT_RECORDING_APPLICATION | Celery; MinIO, ffmpeg, Whisper, SQLAlchemy, outbox | Claims with a committed state transition; commits terminal state/artifact/outbox together | Celery adapter invoking one execution use case |
| `tasks.video_tasks.process_video_task` and `routers.videos.upload_video` | COMPATIBILITY_ADAPTER mixed with PROCESSING_APPLICATION | FastAPI/Celery/local filesystem/SQLAlchemy | Preserves synchronous upload insert and asynchronous task polling | Isolated direct-upload compatibility adapter and use case |
| `services.object_storage` | MEDIA_OR_PROVIDER_ADAPTER | Worker; S3-compatible client | No database/session ownership | Object-storage media-source adapter |
| `services.video_processing` | MEDIA_OR_PROVIDER_ADAPTER mixed with ARTIFACT_ADAPTER | Worker; ffmpeg, Whisper, transcript rows | Transcript helper committed directly | Whisper transcriber adapter and SQLAlchemy artifact adapters |
| `services.processing_outbox` | RESULT_RECORDING_APPLICATION mixed with RESULT_EVENT_CODEC and RESULT_OUTBOX_PERSISTENCE | Worker; SQLAlchemy outbox row | Added result intent to worker transaction | Result-delivery feature (follow-on commit) |
| `services.processing_outbox_publisher` | RESULT_EVENT_CODEC mixed with RESULT_PUBLISHER_ADAPTER | Relay; Kafka producer | Bounded producer acknowledgement | Result codec and Kafka publisher adapter (follow-on commit) |
| `services.processing_outbox_relay` | RESULT_DELIVERY_APPLICATION mixed with RESULT_OUTBOX_PERSISTENCE | Manual/automatic relay; SQLAlchemy and publisher | Conditional claim and per-row commits | Relay service plus outbox repository (follow-on commit) |
| `services.processing_outbox_recovery` | RECOVERY_APPLICATION mixed with RESULT_OUTBOX_PERSISTENCE | Automatic relay; SQLAlchemy | Atomic bounded requeue commits | Reconciliation service plus outbox repository (follow-on commit) |
| `services.assistant_ollama`, assistant router/schemas | ASSISTANT_UNTOUCHED | Trusted HTTP endpoint and Ollama | No processing pipeline state | Unchanged |

The critical pre-refactor dependency violations were the direct Celery task import from
processing acceptance, the complete processing algorithm inside the Celery module, and
application decisions expressed through Pydantic/SQLAlchemy/provider objects.

## Processing-owned model and port design

`app.processing.domain` now owns immutable request/execution commands, transcript rows,
artifacts, failures, success/failure outcomes, and the explicit idempotent-skip result. These
models contain no Kafka records, FastAPI request/response types, Celery tasks, SQLAlchemy
models, or Whisper transport values.

The Kafka adapter validates the unchanged `asset.processing.requested` version 1 envelope
and maps it to `ProcessingRequestCommand`. `DispatchProcessingApplicationService` uses a
processing-request repository and `ProcessingTaskDispatcher`. The Celery dispatcher retains
the deterministic `asset-processing-{eventId}` task id and exact object-reference payload.

`process_asset_object` remains the registered Celery task name, but the task now only maps
the payload, invokes `ExecuteProcessingApplicationService`, and maps its neutral result back
to the existing Celery result dictionary. The use case reads linearly: claim processing work,
acquire the referenced object, transcribe/chunk it, construct ordered transcript rows, persist
the artifact, record one terminal outcome, and commit. Provider, media, artifact, and durable
result adapters stay at the edge.

The claim transition is still committed before external media/provider work. Successful
request status, transcript artifact rows, and result intent still commit together. Failure
handling still rolls back partial artifact work before writing failed request state and one
failure result intent. Duplicate tasks still return without downloading or transcribing.

## Direct-upload compatibility

`POST /videos/upload` remains deprecated and keeps its multipart/status/response behavior.
Its filesystem write, legacy `videos` row, and `process_video` dispatch now live behind
`processing.adapters.direct_upload_compatibility`; the normal Kafka path does not import that
adapter. The direct worker task invokes a compatibility-specific use case and retains the
same task name, polling contract, local paths, transcript chunk format, and status writes.

## Canonical result recording and event codec

`RecordProcessingResultApplicationService` is the only processing-outcome-to-result-intent
mapper. It accepts `ProcessingSucceeded` or `ProcessingFailed`, creates the unchanged
`transcript.ready` or `asset.processing.failed` version 1 payload, applies the existing
bounded error sanitization, and appends one durable intent. It never publishes Kafka.

`ProcessingResultEventCodec` owns the transport envelope and allowed payload fields. It
accepts the result feature's neutral event rather than an ORM row. The Kafka publisher owns
topic/client configuration, `acks=all`, producer idempotence, the existing send timeout, and
translation of typed Kafka failures into transport-neutral transient/permanent failures.

## Durable outbox, relay, and reconciliation

`SqlAlchemyProcessingResultOutboxRepository` is the only adapter that maps neutral result
events to `processing_outbox_events` and executes its state transitions. It owns idempotent
append, due selection, conditional `pending -> publishing` claim, published finalization,
normal retry/terminal failure recording, cooled-down failed-row selection, and atomic bounded
requeue. ORM models and sessions are not exposed to processing or result-delivery application
services.

`RelayProcessingResultsApplicationService` is used by both the manual one-shot command and
the automatic process. It claims, publishes through the neutral port, and finalizes or
classifies a failure through the same repository operations. The application service keeps
the existing five-attempt normal relay and configured retry/cooldown/recovery policy values.

`ReconcileFailedProcessingResultsApplicationService` selects only due `transient` rows below
the recovery-cycle limit and conditionally requeues them into the existing pending path.
Permanent, unknown, historical-unclassified, and `recovery_exhausted` rows remain terminal.
This behavior remains separate from Celery retry behavior.

The temporary `app.services.processing_outbox*` forwarding modules were removed after the
manual/automatic relay entrypoints and tests migrated to the feature-owned services. No
Compose, CLI, Celery discovery, application import, or test fixture referenced those service
paths. Automatic and one-shot relay entrypoints keep their existing module/Compose commands.

Crash-age recovery for rows abandoned in `publishing` was not added because the pre-refactor
runtime had no safe age threshold or frozen configuration for it. It remains explicit debt
rather than changing recovery behavior during this refactor.

## Runtime composition and retained entrypoints

`app.bootstrap` now makes composition explicit without a dependency-injection framework:

- `api` creates the FastAPI application, includes processing/compatibility/assistant routers,
  and preserves the existing bounded startup schema retry;
- `consumer` composes request persistence and Celery dispatch, then starts the existing Kafka
  consumer loop;
- `worker` composes media acquisition, Whisper transcription, artifact persistence, canonical
  result recording, and the direct-upload compatibility execution service;
- `relay` composes the shared publisher, relay policy, outbox repository, and reconciler;
- `assistant` exposes the unchanged assistant router without coupling it to processing.

The stable external import/command paths remain `app.main:app`,
`app.core.celery_app`, `python -m app.consumers.asset_processing_consumer`,
`python -m app.relays.processing_outbox_relay`, and
`python -m app.relays.processing_outbox_auto_relay`. They are thin adapters only. Settings
remain in the existing flat `Settings` object because splitting it would add forwarding
configuration without changing ownership; every environment-variable name and default is
unchanged.

The obsolete `processing.composition`, `services.processing_requests`, result-outbox service
wrappers, task-owned processing algorithm, and SQLAlchemy transcript helper were removed only
after repository-wide import-string and command searches showed no remaining callers.

## Remaining FastAPI debt

- There is no crash-age policy for abandoned `publishing` rows.
- SQLAlchemy metadata plus the existing narrow schema upgrader remain in place instead of
  Alembic migrations.
- Kafka rejection still commits malformed/unsupported events without a DLQ.
- Internal HTTP endpoints still require deployment-level service authentication/network
  policy hardening.
- Direct upload and one-shot relay remain deliberate rollback compatibility paths.
