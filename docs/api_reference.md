# API Reference

This branch exposes only the processing-service contract that Repo B needs.

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

## Removed from this branch

These product/demo endpoints are intentionally not exposed in the default runtime:

- `/videos/search`
- `/users/*`
- `/auth/*`
- list/delete style video CRUD endpoints
