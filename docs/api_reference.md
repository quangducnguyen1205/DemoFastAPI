# API Reference

This branch exposes only the processing-service contract that Repo B needs.

Kafka consumption is internal to FastAPI and does not add a public HTTP API. The consumer reads `asset.processing.requested.v1`, validates the envelope, records idempotency by `eventId`, and hands object-reference metadata to Celery.

## Base URL

```text
http://localhost:8000
```

## Active endpoints

### GET `/`

Returns a small service descriptor.

Example:

```json
{
  "message": "AI Knowledge Workspace Processing Service",
  "docs": "/docs",
  "redoc": "/redoc"
}
```

### GET `/health`

Returns:

```json
{"status": "healthy"}
```

### POST `/videos/upload`

Uploads a media file and starts asynchronous processing.

Multipart fields:

| Field | Type | Required | Notes |
|------|------|----------|------|
| `file` | binary | yes | Must be `video/*` for the current upload path. |
| `title` | string | yes | Stored as processing metadata. |
| `owner_id` | integer | no | Accepted for backward compatibility only. |

Response:

```json
{
  "task_id": "b0f1a94c-5177-4b23-a931-225420fd0fb6",
  "status": "processing",
  "video_id": 42
}
```

### GET `/videos/tasks/{task_id}`

Mirrors Celery task state.

Examples:

```json
{"status": "PENDING"}
```

```json
{"status": "SUCCESS", "result": {"status": "ready", "segments": ["..."]}}
```

```json
{"status": "FAILURE", "error": "message"}
```

### GET `/videos/{video_id}`

Returns the persisted processing record for a single upload.

Example:

```json
{
  "id": 42,
  "title": "Lecture Upload",
  "description": null,
  "url": "videos/7a1c2d1a.mp4",
  "path": "videos/7a1c2d1a.mp4",
  "owner_id": 1,
  "status": "ready"
}
```

Notes:

- `owner_id` is legacy compatibility data, not an authorization boundary.
- `status` is the durable processing-state field on the `videos` row.

### GET `/videos/{video_id}/transcript`

Returns ordered transcript rows for the processed upload.

Example:

```json
[
  {
    "id": 1,
    "video_id": 42,
    "segment_index": 0,
    "text": "First transcript chunk.",
    "created_at": "2026-04-19T10:00:00Z"
  }
]
```

Rows are ordered by `segment_index`.

### GET `/internal/processing-requests/{processingRequestId}/transcript-rows`

Returns Kafka-originated transcript artifact rows for a ready processing request. This is a trusted internal service contract used by Spring when handling `transcript.ready`; it is not a public product API and does not make FastAPI the product transcript owner.

`processingRequestId` is the original Spring `asset.processing.requested` event ID stored as `processing_requests.event_id`.

Success response:

```json
[
  {
    "id": "1",
    "video_id": "9d0d6e36-d45f-41dc-8a73-33ebf0f31749",
    "segment_index": 0,
    "start_ms": 0,
    "end_ms": 1240,
    "text": "First processing artifact chunk.",
    "created_at": "2026-06-21T10:00:00Z"
  }
]
```

Rows are ordered by `segment_index` and then internal row id. The response fields are `id`,
`video_id`, `segment_index`, nullable `start_ms`, nullable `end_ms`, `text`, and `created_at`.
Timing is normalized from Whisper seconds to integer milliseconds before persistence. Legacy
artifacts omit timing at rest and are returned with both fields as `null`. For Kafka-originated
artifact rows, `video_id` carries the processing request/event ID for compatibility with the
Spring wire DTO.

Intentional non-success behavior:

- malformed `processingRequestId`: `400`
- unknown processing request: `404`
- request failed or not yet `ready`: `409`
- request marked `ready` without usable artifact rows: `409`

The endpoint is read-only. It does not update processing state, enqueue Celery work, publish
Kafka, or create outbox rows. It does not return raw media paths, MinIO object references,
credentials, stack traces, Celery internals, ownership metadata, provider seconds, or
word-level timing.

Production-grade service-to-service authentication and network policy are not implemented in this phase. Deploy it only on trusted internal networks until that boundary is hardened.

## Internal Kafka intake

- Topic: `asset.processing.requested.v1`
- Consumer group: `fastapi-processing-v1`
- Delivery model: at-least-once
- Idempotency key: `eventId`
- Payload boundary: MinIO/S3 bucket and object key references only, never raw media bytes
- Processing artifacts: transcript segment rows are stored internally by processing request for later completion-event work

## Internal result outbox contracts

FastAPI persists result-event intent for Kafka-originated processing. When explicitly enabled and invoked, a result relay publishes those events to one shared result topic:

```text
asset.processing.result.v1
```

Both `transcript.ready` and `asset.processing.failed` use this topic because they are result events for the same asset aggregate family. `eventType` distinguishes success from failure.

Common outbox envelope fields:

- `eventId`: outbox row `id`
- `eventType`: `transcript.ready` or `asset.processing.failed`
- `eventVersion`: `1`
- `aggregateType`: `ASSET`
- `aggregateId`: asset id
- `eventKey`: asset id
- `causationEventId`: original incoming `asset.processing.requested` event id
- `occurredAt`: outbox row `occurred_at`
- `payload`: event-specific JSON

`transcript.ready` v1 payload:

```json
{
  "assetId": "asset-id",
  "processingRequestId": "incoming-event-id",
  "status": "ready",
  "segmentCount": 12,
  "completedAt": "2026-06-20T00:00:00Z"
}
```

`asset.processing.failed` v1 payload:

```json
{
  "assetId": "asset-id",
  "processingRequestId": "incoming-event-id",
  "status": "failed",
  "errorCode": "PROCESSING_FAILED",
  "errorMessage": "Safe bounded message",
  "completedAt": "2026-06-20T00:00:00Z"
}
```

Result payloads exclude raw media bytes, transcript text/segments, credentials, stack traces, and product authorization data. Transcript text remains in processing artifact rows and can be retrieved through `GET /internal/processing-requests/{processingRequestId}/transcript-rows`.

Kafka message key is the asset id from `eventKey`. The result producer uses `acks=all` and `enable_idempotence=True` to reduce duplicate records caused by producer retries. Publication is still at-least-once because the relay can publish and then crash before marking the outbox row `published`, so Spring consumers must be idempotent by result `eventId`.

FastAPI does not own product metadata, authorization, workspace membership, or asset state. Spring-side result-event consumption is owned by the product repository and remains disabled by default.

## Removed from this branch

These product/demo endpoints are intentionally not exposed in the default runtime:

- `/videos/search`
- `/users/*`
- `/auth/*`
- list/delete style video CRUD endpoints
