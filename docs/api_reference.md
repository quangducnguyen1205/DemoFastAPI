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

## Internal Kafka intake

- Topic: `asset.processing.requested.v1`
- Consumer group: `fastapi-processing-v1`
- Delivery model: at-least-once
- Idempotency key: `eventId`
- Payload boundary: MinIO/S3 bucket and object key references only, never raw media bytes
- Processing artifacts: transcript segment rows are stored internally by processing request for later completion-event work

## Internal result outbox contracts

FastAPI persists pending result-event intent for Kafka-originated processing, but it does not publish those events to Kafka yet.

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

Result payloads exclude raw media bytes, transcript text/segments, credentials, stack traces, and product authorization data. Transcript text remains in processing artifact rows and can be retrieved later through a dedicated internal contract when that phase exists.

FastAPI does not own product metadata, authorization, workspace membership, or asset state. Result-event publication back to Spring is not implemented in this phase.

## Removed from this branch

These product/demo endpoints are intentionally not exposed in the default runtime:

- `/videos/search`
- `/users/*`
- `/auth/*`
- list/delete style video CRUD endpoints
