# Deployment Guide

This guide covers the processing-only branch of Repo A.

## Docker Compose services

- `backend`
- `worker`
- `consumer`
- `result-relay` (one-shot/manual in base Compose; automatic and active in the Project3 overlay)
- `db`
- `redis`
- `youtube-pot-provider` (internal-only PO-token sidecar; no host port)

## Start the processing stack

```bash
docker compose up --build backend worker consumer db redis youtube-pot-provider
```

## Important environment values

- `DATABASE_URL`
- `MEDIA_ROOT`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CELERY_WORKER_PREFETCH_MULTIPLIER`
- `PROCESSING_LEASE_SECONDS` (default: `14400`, valid: `1..604800`)
- `KAFKA_BOOTSTRAP_SERVERS`
- `KAFKA_ASSET_PROCESSING_TOPIC` (default: `asset.processing.requested.v1`)
- `KAFKA_ASSET_PROCESSING_V2_TOPIC` (default: `asset.processing.requested.v2`)
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
- `PROCESSING_OUTBOX_RECOVERY_ENABLED` (default: `false`)
- `PROCESSING_OUTBOX_RECOVERY_INTERVAL_SECONDS` (default: `30`)
- `PROCESSING_OUTBOX_RECOVERY_COOLDOWN_SECONDS` (default: `60`)
- `PROCESSING_OUTBOX_RECOVERY_BATCH_SIZE` (default: `50`)
- `PROCESSING_OUTBOX_RECOVERY_MAX_CYCLES` (default: `3`)
- `OBJECT_STORAGE_ENDPOINT_URL`
- `OBJECT_STORAGE_ACCESS_KEY_ID`
- `OBJECT_STORAGE_SECRET_ACCESS_KEY`
- `OBJECT_STORAGE_REGION`
- `YOUTUBE_MAX_DURATION_SECONDS` (default: `7200`, valid: `1..86400`)
- `YOUTUBE_MAX_FILE_SIZE_BYTES` (default: `1073741824`, valid:
  `1..10737418240`)
- `YOUTUBE_SOCKET_TIMEOUT_SECONDS` (default: `30`, valid: `1..300`)
- `YOUTUBE_ACQUISITION_TIMEOUT_SECONDS` (default: `900`, valid: `1..7200` and
  not less than the socket timeout)
- `YOUTUBE_DOWNLOAD_RETRIES` (default: `2`, valid: `0..10`)
- `YOUTUBE_PO_TOKEN_PROVIDER_URL` (default:
  `http://youtube-pot-provider:4416`; HTTP origin only, with no credentials/path/query)

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

The same consumer group subscribes to the active `asset.processing.requested.v1` and
`asset.processing.requested.v2` topics. V2 does not change the V1 group or V1 task
route, so it does not intentionally replay existing V1 offsets. The consumer commits valid
offsets only after durable request creation and successful Celery handoff. Invalid,
unsupported, or topic/version-mismatched messages are logged with safe identifiers and
committed to avoid blocking the partition because this phase has no DLQ. Processing remains
at-least-once and idempotent by `eventId`; conflicting V2 payload reuse under the same event
ID is rejected and committed.

Both the Kafka V1 object-storage task and V2 YouTube task use late acknowledgement,
`reject_on_worker_lost=true`, and prefetch multiplier `1`. A worker process loss therefore
allows broker redelivery; the database lease remains the execution guard. Redelivery during
an active lease skips MinIO/yt-dlp/ffmpeg/Whisper and polls the database again after a bounded
short countdown. A controlled processing exception is converted to one durable terminal
`failed` V1 result and returns normally, so it does not create an unbounded Celery retry loop.

The local `PROCESSING_LEASE_SECONDS=14400` default is intentionally conservative and is the
outermost bound of the model. Too-short leases can allow two workers to perform the same external
transcription; attempt fencing and result idempotency still prevent duplicate terminal product
effects, but they cannot eliminate duplicated external compute.

Celery's Redis transport defaults to a 3600-second visibility timeout. Redis has no server-side
acknowledgement: `kombu.transport.redis.QoS.append` stamps a delivery with the wall clock once and
`restore_visible()` puts every delivery older than the visibility timeout back on the queue.
Nothing refreshes that stamp while a task executes, and neither worker heartbeats nor
`acks_late=true` extend it. Visibility is a delivery bound, not an execution bound, so with the
processing time limits in place the transport default is too short: a healthy attempt longer than
an hour has its own delivery restored while it is still running.

The Compose defaults therefore set `CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS=12600` across Celery
producers and workers, and configure `PROCESSING_LEASE_RETRY_POLL_SECONDS=300`. Startup fails
unless

```text
PROCESSING_HARD_TIME_LIMIT_SECONDS < CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS < PROCESSING_LEASE_SECONDS
```

Above the hard limit, no healthy attempt can outlive its own delivery. Below the lease, a lost
worker's delivery is back in the queue before the lease it abandoned expires, so recovery latency
stays governed by the lease rather than by the broker; raising visibility to or beyond the lease
would move that latency onto the broker instead. Losing a whole worker or host is still recovered
within the lease: the restored delivery finds an active lease, polls, and reclaims the request
when the lease expires.

The application also accepts poll values from 1 through 300 seconds, fails startup if the poll is
not shorter than visibility, and caps every calculated retry below visibility. Do not set polling
to the full processing lease: Redis can redeliver an unacknowledged ETA repeatedly after
visibility expires, and workers hold ETA messages in memory. If applications share a Redis broker,
keep the visibility settings aligned because the shortest configured value wins.

## YouTube V2 runtime

The container pins `yt-dlp[pin,pin-deno]==2026.8.19` and
`bgutil-ytdlp-pot-provider==1.3.2`. The matching EJS component and Deno runtime are installed
through the yt-dlp extras; `ffmpeg` remains the existing system package. The worker invokes
yt-dlp as `python -m yt_dlp` with an argument list and `shell=False`, explicitly selects the
`mweb` player client, and points the pinned plugin at the internal provider origin. Remote
components, playlists, cache, cookies, and browser cookie stores remain disabled. The immutable
worker dependency set is the plugin allowlist; this is not a generic plugin framework.

The provider image is pinned by digest to the official `1.3.2-deno` artifact. It runs as the
non-root image user, read-only, without Linux capabilities or a published host port, and has a
bounded `/ping` health check. The worker receives only `YOUTUBE_PO_TOKEN_PROVIDER_URL`; no PO
token is configured, logged, or stored in Redis, PostgreSQL, Kafka, MinIO, or environment files.
The provider performs its own content-bound token caching (six-hour default TTL), while yt-dlp
requests the appropriate token binding for each video/context.

FastAPI accepts only a 1–64-character `[A-Za-z0-9_-]+` video ID and constructs the fixed
canonical watch URL internally. Every attempt downloads into its own safely prefixed temporary
directory with a fixed `media.%(ext)s` template. The selected output must be one non-empty,
non-symlink file inside that directory. The directory is removed after success, controlled
failure, timeout, or normal task cancellation, so YouTube media is not retained in MinIO,
`backend/media`, or another FastAPI persistent store.

Supported scope is one public finite video. Completed livestream recordings may pass only
when yt-dlp reports a finite non-live item. Active/upcoming/post-live streams, playlists,
private/deleted/age-restricted/region-blocked media, and authenticated/cookie access are
controlled failures. User cookies and manual PO tokens are intentionally unsupported because
they introduce account/secret state and current tokens may be short-lived and content-bound.
Provider diagnostics are not returned. Stable result codes are
`YOUTUBE_UNAVAILABLE`, `YOUTUBE_LIVE_NOT_SUPPORTED`,
`YOUTUBE_DURATION_LIMIT_EXCEEDED`, `YOUTUBE_SIZE_LIMIT_EXCEEDED`,
`YOUTUBE_ACQUISITION_TIMEOUT`, and `YOUTUBE_ACQUISITION_FAILED`.

The adapter owns one bounded fresh retry after a provider, GVS, network, rate-limit, or unknown
acquisition failure: maximum two complete extraction attempts with a one-second bounded
backoff under the existing total deadline. Each retry uses a new attempt directory and a new
yt-dlp process so it does not reuse a signed media URL. Private/unavailable/live/policy/size and
total-timeout failures are terminal. Celery does not autoretry these controlled outcomes, which
prevents retry multiplication and duplicate terminal results. Format 18 is not a fallback; a
provider-unavailable warning is treated as failure even if yt-dlp can temporarily select it.

Spring actively publishes V2 requests. Result publication remains
`asset.processing.result.v1`; there is no V2 result topic. Public product creation, submitted-URL
normalization, authorization, and duplicate-product policy remain Spring-owned.

Safe operator signals are `youtube_acquisition_strategy`,
`youtube_pot_provider_unavailable`, `youtube_gvs_forbidden`, `youtube_rate_limited`,
`youtube_acquisition_retry`, `youtube_acquisition_success`, and
`youtube_acquisition_terminal_failure`. They contain only the video ID, strategy, attempt,
safe error family, and selected format. yt-dlp stderr, signed Google Video URLs, tokens, cookies,
and complete query strings are never copied into logs or Kafka results.

Run the separate live canary only when the provider and worker image are available:

```bash
make youtube-live-canary
```

This is a `LIVE NETWORK TEST - NOT PART OF NORMAL UNIT TESTS`. It uses a small fixed set of
public finite videos, requires the explicit CLI acknowledgement embedded in the Make target,
downloads actual media bytes, proves metadata separately from byte acquisition, verifies temp
cleanup, prints only safe JSON, and returns non-zero on failure.

Upstream references for the pinned strategy:

- [yt-dlp stable 2026.08.19](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.08.19)
- [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [bgutil-ytdlp-pot-provider 1.3.2](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/releases/tag/1.3.2)

## Project3 cross-compose integration

Base `docker-compose.yml` remains usable by itself for standalone/direct-upload development. For the coherent Project3 async path, ensure the existing image is available and use:

```bash
make up
```

New Project3 integrations must use this Kafka consumer topology. The direct endpoint remains
available for generic standalone and legacy callers during its deprecation period, but current
Spring does not call it; no removal date is assigned.

Migration from the old normal local flow is:

```text
old: compatibility/direct upload -> TRANSCRIPT_READY -> explicit Index transcript
new: make up + Spring make run -> upload -> automatic processing -> automatic indexing -> SEARCHABLE
```

Explicit indexing recovery, one-shot/manual relays, and exact-ID recovery remain supported and are not deprecated.

Expected local startup order:

1. Start Spring infrastructure first, including Kafka, MinIO, topic bootstrap, and bucket bootstrap.
2. Build the updated DemoFastAPI image and start DemoFastAPI with both Compose files so its
   advisory-locked schema upgrade and dual-topic consumer are ready.
3. Start the Spring application or run the manual V1 smoke command.
4. Keep the active Spring V2 producer enabled only while the FastAPI V2 consumer, worker, and
   provider health checks are ready.

The target renders both Compose files, starts `db`, `redis`, and the provider sidecar without
recreating them, then force-recreates `backend`, `worker`, `consumer`, and automatic
`result-relay` with `--no-deps`; both commands pass `--no-build` and `--pull never`. Recreating
only the runtime containers refreshes their attachment when the external Spring network was
replaced while preserving PostgreSQL and the Redis/Celery broker container. The overlay expects
the Spring Compose network to exist as `${SPRING_INFRA_NETWORK:-infra_default}`. DemoFastAPI
`db`, `redis`, and the provider stay on the normal local network.

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

The endpoint returns ordered rows with `id`, `video_id`, `segment_index`, nullable integer-
millisecond `start_ms`/`end_ms`, `text`, and `created_at`. Legacy artifacts return null timing.
It returns `404` for unknown processing requests and `409` when a request is failed, not ready,
or ready without usable transcript artifacts. It is an internal deployment contract only;
production-grade service-to-service authentication and network policy are not implemented in
this phase.

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
make up
```

The automatic relay remains a dedicated long-running process, not behavior inside `backend`, `consumer`, or `worker`. The Project3 overlay coherently sets `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED=true`, `PROCESSING_RESULT_PUBLISHER_ENABLED=true`, and `PROCESSING_OUTBOX_RECOVERY_ENABLED=true`; the process still validates the publication gates at startup. Base Compose preserves disabled reconciliation and the one-shot/manual behavior.

The base one-shot relay uses `PROCESSING_OUTBOX_RELAY_ENABLED` and `PROCESSING_OUTBOX_RELAY_BATCH_SIZE`. The automatic relay uses `PROCESSING_OUTBOX_AUTO_RELAY_ENABLED`, `PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS`, and `PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE` while preserving the same retry/max-attempt settings. Invalid auto interval or batch-size values fail at startup.

Both relay modes claim due `pending` rows, mark them `publishing`, wait for Kafka acknowledgement, then mark them `published`. Publish failures return rows to `pending` with `next_attempt_at` until the unchanged five-attempt limit, after which rows become `failed` with a typed safe disposition. Each row is processed independently, and the database claim transaction is committed before waiting for Kafka. The Kafka producer uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries.

When enabled, the automatic relay first reconciles a bounded batch of due `failed` rows classified `transient`. Eligibility requires the cooldown to have elapsed and the recovery-cycle count to be below the configured maximum. Atomic compare-and-set requeue preserves event identity and payload, increments the recovery cycle, and resets only normal publication-attempt state. A later terminal failure after the final cycle becomes `recovery_exhausted`. `permanent`, `unknown`, historical, and recovery-exhausted rows require manual review. The retained one-shot relay still processes normal pending rows and does not silently opt into reconciliation.

Stuck `publishing` recovery after process interruption and a full Kafka DLQ/parking topic remain future work. Publication is at-least-once rather than end-to-end exactly-once because the relay can publish and then crash before marking the row `published`; Spring consumers must be idempotent by result `eventId`.

The automatic relay publishes only due FastAPI processing-result outbox rows through the existing supported contracts: `transcript.ready` and `asset.processing.failed`. It is not a generic event relay and does not place transcript text, media bytes, object storage credentials, tokens, stack traces, or product ownership data in result payloads. P3-D4 `[ĐÃ SMOKE THỰC TẾ]` verified the automatic relay with the Project3 overlay in the fully automatic Spring/FastAPI path: Spring automatic request relay, FastAPI consumer/Celery, FastAPI automatic result relay, and Spring automatic result listener completed one upload without manual request/result controls. Direct upload was not exercised; current Spring uses only the Kafka/outbox processing path. Indexing/search stayed disabled in that historical run.

This repository does not use Alembic yet. Startup uses SQLAlchemy metadata for new databases and
idempotent narrow schema upgrades for processing-request source shapes, processing leases,
processing-outbox recovery metadata, and nullable transcript timing on existing local
databases. Legacy request rows become `OBJECT_STORAGE`; their object references are retained.
Legacy attempt counts become zero; non-processing lease timestamps remain null; legacy
`processing` rows become immediately reclaimable without a fabricated start time. Pre-existing
failed outbox rows become `unknown`, retain event identity, and are never automatically
reconciled. Do not edit rows directly; investigate recovery-exhausted failures and use retained
operator controls only after the publisher dependency is healthy.

## Runtime validation

```bash
python -m compileall backend/app
docker compose config
```

The normal unit suite mocks Kafka, MinIO, yt-dlp, and Whisper boundaries; it requires no live
provider. Validate the image separately by importing the pinned yt-dlp package, then use
controlled provider/runtime characterization only when outbound access and a public fixture
are explicitly available.

## Branch-specific note

This branch is not meant to run a frontend or a search stack. If you are looking for product-facing behavior, use Repo B and Repo FE.

Automatic Spring Kafka listener consumption exists in the product repository but remains disabled by default. Enable it only as part of a controlled local integration run.
