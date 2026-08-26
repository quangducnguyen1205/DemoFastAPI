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

The Kafka adapter still validates the unchanged `asset.processing.requested` version 1
envelope and maps it to the explicit object-storage `ProcessingRequestCommand`. It also
subscribes, with the same consumer group, to active topic
`asset.processing.requested.v2`. An exact version 2 event with `sourceType=YOUTUBE` maps to
the separate `YouTubeProcessingRequestCommand`; arbitrary URLs, object-storage fields, extra
payload fields, and unsafe video IDs are rejected. `DispatchProcessingApplicationService`
uses the same processing-request repository and source-aware `ProcessingTaskDispatcher`.
The V1 Celery dispatcher retains the deterministic `asset-processing-{eventId}` task id and
exact object-reference payload.

`process_asset_object` remains the registered Celery task name, but the task now only maps
the payload, invokes `ExecuteProcessingApplicationService`, and maps its neutral result back
to the existing Celery result dictionary. The use case reads linearly: claim processing work,
acquire the referenced object, transcribe/chunk it, construct ordered transcript rows, persist
the artifact, record one terminal outcome, and commit. Provider, media, artifact, and durable
result adapters stay at the edge.

`process_youtube_asset` is a separate registered task with a payload containing only event,
asset, workspace, owner, and validated YouTube video IDs. It invokes the same execution use
case, lease store, Whisper adapter, transcript artifact store, and result recorder. It differs
only at the media-source adapter. No generic provider registry or nullable command bag was
introduced.

The claim transition is still committed before external media/provider work, but it is now a
finite execution lease. Successful request status, transcript artifact rows, and result intent
still commit together. Failure handling still rolls back partial artifact work before writing
failed request state and one failure result intent. Duplicate tasks with an active lease return
without downloading or transcribing.

## Processing lease and crash recovery

`processing_requests` stores nullable `processing_started_at` and `lease_expires_at` plus a
non-negative `attempt_count`. A conditional database update can claim `accepted` or `enqueued`,
or reclaim `processing` only when `lease_expires_at <= now`. The winning update sets both
timestamps, increments `attempt_count`, and commits before MinIO, ffmpeg, or Whisper work.
An active lease and terminal `ready`/`failed` state cannot be claimed. Legacy non-processing
rows retain null timestamps and attempt zero; a legacy `processing` row receives only an
immediately expired lease during schema upgrade, without fabricating a processing start time.

The acquired attempt count is also a fencing token. A terminal update must still match
`status=processing` and the acquired attempt. This makes the database decide races between an
expired attempt completing late and its replacement: only one attempt can store the terminal
status, replace transcript artifacts, and append result intent. Success sets `ready`, replaces
any incomplete integration artifact rows, records the existing `transcript.ready` v1 intent,
and clears both lease timestamps in one transaction. Controlled failure stores a sanitized
diagnostic, records the existing `asset.processing.failed` v1 intent, and clears both timestamps
in one transaction. The result outbox uniqueness contract remains unchanged.

`PROCESSING_LEASE_SECONDS` is finite, validated from 1 through 604800 seconds, and defaults to
14400 seconds (four hours) for conservative local Whisper execution. Operators must coordinate
it with any future Celery task limits and measured media-size/Whisper throughput. Both
object-storage and YouTube tasks use one-message prefetch, late acknowledgement, and
worker-lost rejection/requeue.

Redis transport keeps its one-hour visibility timeout explicit through
`CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS=3600`. Active-lease redelivery does not schedule a
countdown for the remaining four-hour lease. It publishes one successor after at most
`PROCESSING_LEASE_RETRY_POLL_SECONDS=300`, acknowledges the current delivery through Celery's
normal `Retry` handling, and checks the database again. Configuration rejects a poll interval
outside 1–300 seconds or greater than or equal to the broker visibility timeout, and the delay
calculation also caps defensively below that timeout. Thus a retry ETA cannot cross Redis
visibility and be restored repeatedly while an old copy remains scheduled in worker memory.

`max_retries=None` is intentional for worker-loss recovery, but does not make a controlled
failure retry forever. The database lease has a finite expiry, each delivery creates at most
one successor, and the chain stops at reclaim or any terminal state. A numeric Celery retry
limit could strand a request after enough worker losses; terminal controlled failures instead
return normally and are acknowledged. Repeated independent broker deliveries may each create
one short polling chain, but this design has no branching retry amplification. The existing
database claim and attempt fence still prevent MinIO/yt-dlp/ffmpeg/Whisper work while a lease
is active. Lease expiry can permit duplicate external execution if an original attempt
outlives the configured duration, but the attempt fence and result-outbox idempotency protect
the canonical product/result effects.

## Source-shaped processing integration state

`processing_requests.source_type` is non-null and constrained to one of two complete shapes:

- `OBJECT_STORAGE` has no `youtube_video_id` and requires bucket, object key, content type,
  and non-negative size. Existing rows are backfilled to this source.
- `YOUTUBE` requires a 1–64-character `[A-Za-z0-9_-]+` video ID and requires all object
  storage columns, including original filename, to be null.

Fresh schema creation uses SQLAlchemy metadata. Existing PostgreSQL tables receive the new
columns, backfill, nullability changes, and stable named constraints inside the existing
session advisory lock. Constraint-existence checks are scoped to the current schema and the
real `processing_requests` table. The narrow SQLite upgrader used by tests/local compatibility
is also idempotent. Lease columns, attempt fencing, event uniqueness, transcript relations,
and result-outbox behavior are retained.

## Temporary YouTube acquisition

`YouTubeProcessingMediaSource` receives only the validated video ID and constructs
`https://www.youtube.com/watch?v={youtubeVideoId}` internally. It runs the explicitly pinned
`yt-dlp[pin,pin-deno]==2026.8.19` module through an argument-list subprocess with `shell=False`.
The subprocess strategy was selected over the embedding API to provide a hard wall-clock
deadline, process-group termination on worker cancellation/timeout, and a directory-size
guard without sharing provider-global state. The pinned extras include the matching local EJS
component and Deno runtime required by current YouTube extraction. The worker image also pins
the single required `bgutil-ytdlp-pot-provider==1.3.2` plugin and selects the `mweb` client.
The plugin calls a digest-pinned internal provider sidecar; remote components and all
account/cookie inputs are disabled.

Every attempt owns a `TemporaryDirectory` whose prefix is derived from hashed internal event
and asset IDs. The output template is fixed as `media.%(ext)s`; provider titles never become
paths. Playlists, cookies, browser cookie stores, cache, arbitrary URLs, and caller-controlled
templates are disabled. Metadata is checked before download for exact ID, finite duration,
live state, and known size. The final regular, non-symlink file must remain inside the owned
directory, be the single unambiguous output, and satisfy the size limit. Directory context
cleanup removes media, `.part`, metadata, and other temporary files after success, controlled
failure, timeout, or normal cancellation.

Defaults are two bounded retries for every yt-dlp retry category, a 30-second socket timeout,
a 900-second total acquisition deadline, a two-hour duration limit, and a 1 GiB file limit.
Public finite single videos and completed recordings that behave as finite videos are
supported. Active/upcoming/post-live streams are rejected. Private, deleted, age-restricted,
region-blocked, and authenticated/cookie-only media are unsupported.

The media-source adapter is the sole fresh-retry owner. Provider/GVS/network/rate-limit and
unknown failures get at most one new complete extraction, for two attempts total with a bounded
one-second backoff inside the existing deadline. Each attempt has a fresh subdirectory and
yt-dlp process. Permanent policy/unavailable/live/size failures and total deadline expiry are
not retried. Celery records the exhausted controlled failure once and does not layer autoretry
on top. Format 18 is not an application fallback and provider-unavailable diagnostics fail the
attempt rather than silently using it.

Provider output is never copied into a result. Controlled failures map to stable codes:
`YOUTUBE_UNAVAILABLE`, `YOUTUBE_LIVE_NOT_SUPPORTED`,
`YOUTUBE_DURATION_LIMIT_EXCEEDED`, `YOUTUBE_SIZE_LIMIT_EXCEEDED`,
`YOUTUBE_ACQUISITION_TIMEOUT`, and `YOUTUBE_ACQUISITION_FAILED`. Each records one terminal
failed V1 result and returns normally from Celery; worker/process loss remains a lease and
broker-redelivery concern.

The V2 path is active and Spring publishes the V2 request. FastAPI does not own
public YouTube product creation, URL normalization, authorization, or duplicate-product
policy. Acquired media is temporary and not retained. Successful and failed outcomes continue
to use `asset.processing.result.v1` and the existing V1 envelope/payload.

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
configuration without changing ownership. Existing environment names/defaults are unchanged;
the V2 topic and bounded YouTube acquisition settings are additive.

The obsolete `processing.composition`, `services.processing_requests`, result-outbox service
wrappers, task-owned processing algorithm, and SQLAlchemy transcript helper were removed only
after repository-wide import-string and command searches showed no remaining callers.

## Remaining FastAPI debt

- There is no crash-age policy for abandoned `publishing` rows.
- Spring publishes the active V2 request contract; FastAPI remains only its processing owner.
- Public YouTube product creation, duplicate policy, authorized retry API, and frontend source
  entry remain outside this repository.
- SQLAlchemy metadata plus the existing narrow schema upgrader remain in place instead of
  Alembic migrations.
- Kafka rejection still commits malformed/unsupported events without a DLQ.
- Internal HTTP endpoints still require deployment-level service authentication/network
  policy hardening.
- Direct upload remains standalone/legacy compatibility; the one-shot relay remains a deliberate
  recovery path. Current Spring has no direct-upload mode.
