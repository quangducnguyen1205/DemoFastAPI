# API Reference

This document describes the currently supported REST API for the Video Similarity Search backend. All endpoints are served from the FastAPI app inside `docker compose` on port `8000`.

## Base URL

```
http://localhost:8000
```

Interactive documentation:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

Authentication is not yet enforced; all routes are publicly accessible in development deployments.

---

## Global Endpoints

### GET `/`
- **Purpose:** Simple landing endpoint confirming the service is running and linking to interactive docs.
- **Response 200 Example:**
  ```json
  {
    "message": "Welcome to User Management API",
    "docs": "/docs",
    "redoc": "/redoc"
  }
  ```

### GET `/health`
- **Purpose:** Health probe for load balancers and uptime monitors.
- **Response 200 Example:**
  ```json
  {"status": "healthy"}
  ```

---

## User Endpoints

### POST `/users/`
- **Purpose:** Create a basic user record. Used mainly to associate uploaded videos with owners.
- **Request Body:**
  ```json
  {
    "name": "Ada Lovelace",
    "email": "ada@example.com"
  }
  ```
- **Response 200 Example:**
  ```json
  {
    "id": 1,
    "name": "Ada Lovelace",
    "email": "ada@example.com"
  }
  ```
- **Notes:** Returns `400` if an email already exists.

### GET `/users/{user_id}`
- **Purpose:** Retrieve a single user by numeric ID.
- **Path Parameter:** `user_id` (integer)
- **Response 200 Example:**
  ```json
  {
    "id": 1,
    "name": "Ada Lovelace",
    "email": "ada@example.com"
  }
  ```
- **Errors:** `404` when the user does not exist.

---

## Video Endpoints

### POST `/videos/upload`
- **Purpose:** Upload a video file and enqueue transcription + embedding generation. Work is offloaded to a Celery worker so the HTTP request returns immediately.
- **Form Data (multipart):**
  | Field | Type | Required | Description |
  |-------|------|----------|-------------|
  | `file` | Binary file | Yes | MP4 (or other ffmpeg-supported) video. |
  | `title` | string | Yes | Display name for the video. |
  | `owner_id` | integer | No | Optional user ID to tag ownership. |
- **Response 200 Example:**
  ```json
  {
    "task_id": "b0f1a94c-5177-4b23-a931-225420fd0fb6",
    "status": "processing",
    "video_id": 42
  }
  ```
- **Background Processing:**
  1. File saved under `MEDIA_ROOT/videos/` with a UUID filename.
  2. Database row created with `status="processing"` and optional `owner_id`.
  3. Celery task `process_video_task` receives `(video_id, absolute_path)` to run Whisper, segment transcripts, generate embeddings, and update the FAISS index.

### GET `/videos/tasks/{task_id}`
- **Purpose:** Poll Celery for task status that was returned from `/videos/upload`.
- **Path Parameter:** `task_id` — UUID string returned in the upload response.
- **Responses:**
  - `{"status": "PENDING"}` — task created but not started.
  - `{"status": "SUCCESS", "result": {...}}` — includes any payload returned by the worker (such as final status info).
  - `{"status": "FAILURE", "error": "message"}` — worker failed; `error` contains text traceback summary.
- **Notes:** This endpoint mirrors Celery states and does not mutate data.

### GET `/videos/`
- **Purpose:** List stored video metadata with optional ownership filtering.
- **Query Parameters:**
  | Name | Type | Default | Description |
  |------|------|---------|-------------|
  | `skip` | integer | `0` | Offset for pagination. |
  | `limit` | integer | `100` | Maximum rows to return. |
  | `owner_id` | integer | `None` | When provided, only videos owned by that user are returned. |
- **Response 200 Example:**
  ```json
  [
    {
      "id": 42,
      "title": "Demo Upload",
      "description": null,
      "url": "videos/7a1c2d1a.mp4",
      "path": "videos/7a1c2d1a.mp4",
      "owner_id": 1,
      "status": "ready"
    }
  ]
  ```

### GET `/videos/{video_id}`
- **Purpose:** Fetch a single video record by ID.
- **Path Parameter:** `video_id` (integer)
- **Response 200 Example:**
  ```json
  {
    "id": 42,
    "title": "Demo Upload",
    "description": null,
    "url": "videos/7a1c2d1a.mp4",
    "path": "videos/7a1c2d1a.mp4",
    "owner_id": 1,
    "status": "ready"
  }
  ```
- **Errors:** `404` when no record exists.

### DELETE `/videos/{video_id}`
- **Purpose:** Remove a video record and (when possible) delete the associated media file.
- **Response 200 Example:**
  ```json
  {
    "message": "Video deleted successfully",
    "id": 42
  }
  ```
- **Notes:** If the stored file path does not resolve under the configured video directory, deletion is skipped and logged.

---

## Search Endpoint

### GET `/videos/search`
- **Purpose:** Semantic search across processed video transcripts using FAISS vectors.
- **Query Parameters:**
  | Name | Type | Required | Default | Description |
  |------|------|----------|---------|-------------|
  | `q` | string | Yes | — | Natural-language query text. Must be non-empty. |
  | `k` | integer | No | `5` | Number of videos to return (top-k). |
  | `owner_id` | integer | No | `None` | When set, results whose `owner_id` does not match are dropped after FAISS ranking. |
- **Example Request:**
  ```bash
  curl "http://localhost:8000/videos/search?q=deep%20learning&k=3&owner_id=1"
  ```
- **Response 200 Example:**
  ```json
  [
    {
      "video_id": 42,
      "title": "Transformer Primer",
      "path": "videos/7a1c2d1a.mp4",
      "similarity_score": 0.87
    }
  ]
  ```
- **Error Cases:**
  - `400` when `q` is blank or whitespace.
  - `500` when FAISS or mapping files cannot be loaded.
- **Processing Notes:**
  1. Query embedding generated via sentence-transformer.
  2. `load_index_if_exists` loads a read-only FAISS index.
  3. Search retrieves more transcript segments than requested to allow grouping.
  4. Segment IDs are mapped back to `video_id` using the pickled mapping file.
  5. For each video, the highest similarity score is retained, results are sorted, `owner_id` filtering is applied (if provided), and the top `k` entries are returned.

---

## Error Handling

- All endpoints raise `HTTPException` with an informative `detail` field when validation fails (`400`/`422`) or rows are missing (`404`).
- Unexpected server-side failures log warnings and return `500` with a generic message to avoid leaking implementation details.
- Semantic search gracefully returns an empty list when the FAISS index contains no vectors yet.

---

## Example cURL Workflow

```bash
# 1. Create a user
curl -s -X POST http://localhost:8000/users/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo", "email": "demo@example.com"}'

# 2. Upload a video and associate with that user
curl -s -X POST http://localhost:8000/videos/upload \
  -F "file=@sample.mp4" \
  -F "title=Sample Talk" \
  -F "owner_id=1"

# 3. Poll the task status
curl -s http://localhost:8000/videos/tasks/<task_id>

# 4. Search across processed videos, restricting to the owner
curl -s "http://localhost:8000/videos/search?q=transformer&owner_id=1"
```
