# API Reference

## Table of Contents
1. [Base URL](#base-url)
2. [Authentication](#authentication)
3. [Health & Status](#health--status)
4. [User Endpoints](#user-endpoints)
5. [Video Endpoints](#video-endpoints)
6. [Search Endpoint](#search-endpoint)
7. [Task Status Endpoint](#task-status-endpoint)
8. [Error Responses](#error-responses)

---

## Base URL

**Development (Docker Compose):**
```
http://localhost:8000
```

**Interactive Documentation:**
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## Authentication

**Current Implementation:** None (open API)

**Future Consideration:** JWT-based authentication for user-specific operations.

---

## Health & Status

### GET `/`

**Description:** Root endpoint with API information.

**Response (200 OK):**
```json
{
  "message": "Welcome to User Management API",
  "docs": "/docs",
  "redoc": "/redoc"
}
```

### GET `/health`

**Description:** Health check endpoint for monitoring.

**Response (200 OK):**
```json
{
  "status": "healthy"
}
```

**Use Case:** Load balancer health checks, uptime monitoring.

---

## User Endpoints

### POST `/users/`

**Description:** Create a new user account.

**Request Body:**
```json
{
  "username": "john_doe",
  "email": "john@example.com"
}
```

**Response (200 OK):**
```json
{
  "id": 1,
  "username": "john_doe",
  "email": "john@example.com",
  "created_at": "2025-09-14T10:30:00Z"
}
```

**Error Responses:**
- `422 Unprocessable Entity` — Invalid request body
- `500 Internal Server Error` — Database constraint violation (duplicate username/email)

---

### GET `/users/`

**Description:** List all users (paginated).

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| skip | integer | 0 | Number of records to skip |
| limit | integer | 100 | Maximum records to return |

**Example Request:**
```bash
curl "http://localhost:8000/users/?skip=0&limit=10"
```

**Response (200 OK):**
```json
[
  {
    "id": 1,
    "username": "alice",
    "email": "alice@example.com",
    "created_at": "2025-09-13T08:00:00Z"
  },
  {
    "id": 2,
    "username": "bob",
    "email": "bob@example.com",
    "created_at": "2025-09-14T09:15:00Z"
  }
]
```

---

### GET `/users/{user_id}`

**Description:** Get a single user by ID.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| user_id | integer | User's unique identifier |

**Example Request:**
```bash
curl "http://localhost:8000/users/1"
```

**Response (200 OK):**
```json
{
  "id": 1,
  "username": "alice",
  "email": "alice@example.com",
  "created_at": "2025-09-13T08:00:00Z"
}
```

**Error Responses:**
- `404 Not Found` — User does not exist

---

### PUT `/users/{user_id}`

**Description:** Update an existing user (full update).

**Path Parameters:** `user_id` (integer)

**Request Body:**
```json
{
  "username": "alice_updated",
  "email": "alice_new@example.com"
}
```

**Response (200 OK):**
```json
{
  "id": 1,
  "username": "alice_updated",
  "email": "alice_new@example.com",
  "created_at": "2025-09-13T08:00:00Z"
}
```

**Error Responses:**
- `404 Not Found` — User does not exist

---

### DELETE `/users/{user_id}`

**Description:** Delete a user account.

**Path Parameters:** `user_id` (integer)

**Example Request:**
```bash
curl -X DELETE "http://localhost:8000/users/1"
```

**Response (200 OK):**
```json
{
  "detail": "User deleted"
}
```

**Error Responses:**
- `404 Not Found` — User does not exist

---

## Video Endpoints

### POST `/videos/upload`

**Description:** Upload a video file for processing. Returns immediately with a task ID for background transcription and embedding generation.

**Request:**
- **Content-Type:** `multipart/form-data`

**Form Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | File | Yes | Video file (mp4, avi, mov, etc.) |
| title | string | Yes | Video title |
| owner_id | integer | No | User ID of uploader |

**Example Request (curl):**
```bash
curl -X POST "http://localhost:8000/videos/upload" \
  -F "file=@/path/to/video.mp4" \
  -F "title=Introduction to Machine Learning" \
  -F "owner_id=1"
```

**Example Request (Python):**
```python
import requests

with open("video.mp4", "rb") as f:
    response = requests.post(
        "http://localhost:8000/videos/upload",
        files={"file": f},
        data={"title": "My Video", "owner_id": 1}
    )
print(response.json())
```

**Response (200 OK):**
```json
{
  "task_id": "7f9d8e6c-5b4a-3c2d-1e0f-a9b8c7d6e5f4",
  "status": "processing",
  "video_id": 42
}
```

**Fields Explained:**
- `task_id` — Celery task identifier (use to check progress)
- `status` — Initial status ("processing")
- `video_id` — Database record ID (use for future queries)

**Error Responses:**
- `422 Unprocessable Entity` — Missing required fields or invalid file
- `500 Internal Server Error` — File save failed or database error

**Processing Pipeline:**
1. Save file to `media/videos/` directory
2. Create database record with `status="processing"`
3. Enqueue Celery task for:
   - Audio extraction (ffmpeg)
   - Transcription (Whisper)
   - Segmentation
   - Embedding generation (sentence-transformers)
   - FAISS index update
4. Update video status to `"ready"` or `"failed"`

---

### GET `/videos/tasks/{task_id}`

**Description:** Check the status of a background video processing task.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| task_id | string (UUID) | Task identifier from upload response |

**Example Request:**
```bash
curl "http://localhost:8000/videos/tasks/7f9d8e6c-5b4a-3c2d-1e0f-a9b8c7d6e5f4"
```

**Response (200 OK) — Processing:**
```json
{
  "status": "PENDING"
}
```

**Response (200 OK) — Success:**
```json
{
  "status": "SUCCESS",
  "result": {
    "status": "ready",
    "segments": [
      "Welcome to this tutorial on machine learning.",
      "In this video, we will cover supervised learning algorithms.",
      "Let's start with linear regression."
    ]
  }
}
```

**Response (200 OK) — Failure:**
```json
{
  "status": "FAILURE",
  "error": "Transcription failed: Whisper model loading error"
}
```

**Possible Status Values:**
| Status | Description |
|--------|-------------|
| PENDING | Task queued, not yet started |
| STARTED | Worker picked up task |
| SUCCESS | Task completed successfully |
| FAILURE | Task failed with error |
| RETRY | Task will be retried |

---

### GET `/videos/search`

**Description:** Semantic search over video transcripts using FAISS vector similarity.

**Query Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| q | string | Yes | — | Search query text |
| k | integer | No | 5 | Number of videos to return |

**Example Request:**
```bash
curl "http://localhost:8000/videos/search?q=neural%20networks&k=3"
```

**Response (200 OK):**
```json
[
  {
    "video_id": 15,
    "title": "Deep Learning Fundamentals",
    "path": "videos/abc123.mp4",
    "similarity_score": 0.89
  },
  {
    "video_id": 7,
    "title": "Introduction to AI",
    "path": "videos/def456.mp4",
    "similarity_score": 0.76
  },
  {
    "video_id": 23,
    "title": "Computer Vision Tutorial",
    "path": "videos/ghi789.mp4",
    "similarity_score": 0.68
  }
]
```

**Fields Explained:**
- `video_id` — Database record ID
- `title` — Video title from metadata
- `path` — Relative file path (can be used to construct download URL)
- `similarity_score` — Semantic similarity (0–1, higher = more relevant)

**Search Algorithm:**
1. Generate query embedding using sentence-transformers
2. Search FAISS index for top-N nearest vectors (N = k × 4)
3. Map segment IDs to video IDs
4. Group by video, keep highest similarity per video
5. Return top k videos sorted by score

**Error Responses:**
- `400 Bad Request` — Empty query string
- `500 Internal Server Error` — FAISS index not found or corrupted

**Note:** If no videos have been processed yet, returns empty array `[]`.

---

### POST `/videos/`

**Description:** Manually create a video record (without file upload).

**Request Body:**
```json
{
  "title": "Sample Video",
  "description": "A sample video description",
  "url": "videos/sample.mp4"
}
```

**Response (200 OK):**
```json
{
  "id": 10,
  "title": "Sample Video",
  "description": "A sample video description",
  "url": "videos/sample.mp4",
  "path": null,
  "owner_id": null,
  "status": null,
  "created_at": "2025-09-14T12:00:00Z",
  "updated_at": null
}
```

---

### GET `/videos/`

**Description:** List all videos (paginated).

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| skip | integer | 0 | Offset for pagination |
| limit | integer | 100 | Max records to return |

**Example Request:**
```bash
curl "http://localhost:8000/videos/?skip=0&limit=10"
```

**Response (200 OK):**
```json
[
  {
    "id": 1,
    "title": "Introduction to Python",
    "description": "Learn Python basics",
    "url": "videos/python_intro.mp4",
    "path": "videos/abc123.mp4",
    "owner_id": 5,
    "status": "ready",
    "created_at": "2025-09-10T08:30:00Z",
    "updated_at": "2025-09-10T08:35:00Z"
  },
  {
    "id": 2,
    "title": "Advanced JavaScript",
    "description": null,
    "url": "videos/js_advanced.mp4",
    "path": "videos/def456.mp4",
    "owner_id": 3,
    "status": "processing",
    "created_at": "2025-09-12T14:20:00Z",
    "updated_at": null
  }
]
```

---

### GET `/videos/{video_id}`

**Description:** Get a single video by ID.

**Path Parameters:** `video_id` (integer)

**Example Request:**
```bash
curl "http://localhost:8000/videos/1"
```

**Response (200 OK):**
```json
{
  "id": 1,
  "title": "Introduction to Python",
  "description": "Learn Python basics",
  "url": "videos/python_intro.mp4",
  "path": "videos/abc123.mp4",
  "owner_id": 5,
  "status": "ready",
  "created_at": "2025-09-10T08:30:00Z",
  "updated_at": "2025-09-10T08:35:00Z"
}
```

**Error Responses:**
- `404 Not Found` — Video does not exist

---

### PUT `/videos/{video_id}`

**Description:** Update video metadata (does not re-process file).

**Path Parameters:** `video_id` (integer)

**Request Body:**
```json
{
  "title": "Updated Title",
  "description": "Updated description",
  "url": "videos/new_path.mp4"
}
```

**Response (200 OK):**
```json
{
  "id": 1,
  "title": "Updated Title",
  "description": "Updated description",
  "url": "videos/new_path.mp4",
  "path": "videos/abc123.mp4",
  "owner_id": 5,
  "status": "ready",
  "created_at": "2025-09-10T08:30:00Z",
  "updated_at": "2025-09-14T10:00:00Z"
}
```

---

### DELETE `/videos/{video_id}`

**Description:** Delete a video record and associated file.

**Path Parameters:** `video_id` (integer)

**Example Request:**
```bash
curl -X DELETE "http://localhost:8000/videos/1"
```

**Response (200 OK):**
```json
{
  "detail": "Video deleted"
}
```

**Side Effects:**
- Removes video file from `media/videos/` directory (if path is safe)
- Deletes all associated transcript segments (cascade)
- **Does not** remove embeddings from FAISS index (requires manual rebuild)

**Error Responses:**
- `404 Not Found` — Video does not exist

---

## Error Responses

### Standard Error Format

All errors return a JSON object with a `detail` field:

```json
{
  "detail": "Error message here"
}
```

### HTTP Status Codes

| Code | Meaning | Example Scenario |
|------|---------|------------------|
| 200 | Success | Request completed successfully |
| 400 | Bad Request | Empty search query, invalid parameters |
| 404 | Not Found | User/video ID does not exist |
| 422 | Unprocessable Entity | Invalid request body (Pydantic validation) |
| 500 | Internal Server Error | Database connection failed, FAISS error |

### Example Error Response

**Request:**
```bash
curl "http://localhost:8000/videos/search?q="
```

**Response (400 Bad Request):**
```json
{
  "detail": "Query parameter 'q' cannot be empty"
}
```

---

## Rate Limiting

**Current Implementation:** None

**Future Consideration:** Implement rate limiting to prevent abuse:
- 100 requests/minute per IP for search
- 10 uploads/hour per user

---

## API Versioning

**Current Version:** No explicit versioning (v1 implicit)

**Future Strategy:** Use URL path versioning (`/api/v2/videos/...`) when introducing breaking changes.

---

## Summary

This API provides:

✅ **User management** — CRUD operations for user accounts  
✅ **Video upload** — Asynchronous processing with task tracking  
✅ **Semantic search** — Content-based similarity using FAISS  
✅ **Task monitoring** — Real-time status updates via Celery  

For system architecture details, see [architecture.md](./architecture.md).  
For deployment instructions, see [deployment_guide.md](./deployment_guide.md).
