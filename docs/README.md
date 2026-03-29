# Video Similarity Search System — Documentation Hub

A FastAPI backend that ingests raw video files, transcribes their audio with Whisper, embeds transcript segments, and serves semantic search results via FAISS. Everything ships in Docker Compose for reproducible local deployments.

## Project Overview Diagram

```
Client (curl/Swagger) --> FastAPI (users + videos routers)
                        |            \
                        |             --> PostgreSQL (metadata)
                        |             --> Media volume (video files)
                        |             --> Celery Task Queue (Redis broker)
                        |                             \
                        |                              --> Worker (Whisper + embeddings + FAISS writer)
                        |
                        --> FAISS Reader (semantic search)
```

The API surface is intentionally small: a root + health probe, minimal user CRUD (create + read), and the core video routes (upload, task polling, list/detail/delete, search).

---

## Quickstart (Docker Compose)

1. **Clone & enter the repo**
   ```bash
   git clone <repository-url>
   cd DemoFirstBackend
   ```
2. **Create environment config**
   ```bash
   cp .env.example .env
   # defaults target the compose services (Postgres, Redis, media volume)
   ```
3. **Launch the stack**
   ```bash
   docker compose up --build
   ```
   Starts Postgres, Redis, the FastAPI `backend`, and the Celery `worker`. Ports exposed: `8000` (API), `5432` (db), `6379` (Redis).
   The backend and worker now share one built Python runtime image, so Compose no longer exports two separate heavyweight ML images for the same app code.
4. **Explore the API**
   - Health: `curl http://localhost:8000/health`
   - Swagger: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc

Stop everything with `docker compose down`. Add `-v` to drop local database and media volumes.

---

## Uploads & Background Processing

1. **Upload** — POST `/videos/upload` with multipart form data (`file`, `title`, optional `owner_id`). The endpoint saves the binary to `MEDIA_ROOT/videos/`, writes a `Video` row with `status="processing"`, and emits a Celery task ID.
2. **Celery Worker** — The worker receives `(video_id, absolute_path)` and performs:
   - audio extraction with ffmpeg
   - transcription via a process-local cached Whisper model
   - transcript segmentation + persistence
   - batched embedding generation (sentence-transformers)
   - FAISS index + mapping updates
   - final status update (`ready` or `failed`)
   - lightweight timing logs for the major hot-path stages
3. **Threadpool Façade** — Even though routers are async, heavy database/file work stays synchronous and executes inside `run_in_threadpool`, keeping the FastAPI event loop responsive.
4. **Contract Stability** — This internal performance pass preserves the existing HTTP contract for `POST /videos/upload`, `GET /videos/tasks/{task_id}`, `GET /videos/{video_id}`, and `GET /videos/{video_id}/transcript`.

Use this cURL snippet after the stack is running:
```bash
curl -X POST http://localhost:8000/videos/upload \
  -F "file=@sample.mp4" \
  -F "title=Demo Upload" \
  -F "owner_id=1"
```
Response contains `task_id`, `video_id`, and `status="processing"`.

---

## Checking Task Status

Poll the Celery state machine through `GET /videos/tasks/{task_id}`. Typical responses:
- `{"status": "PENDING"}` — queueing or waiting for a worker
- `{"status": "SUCCESS", "result": {...}}` — processing completed (the worker may include extra payload)
- `{"status": "FAILURE", "error": "..."}` — inspect `docker compose logs worker` for details

Workers persist their progress only through the database/video status; task results are informational.

---

## Listing & Fetching Videos

- `GET /videos/` returns paginated metadata. Query parameters: `skip`, `limit`, and optional `owner_id` filter.
- `GET /videos/{video_id}` fetches a single record; `DELETE` removes it (and attempts to remove the media file inside the mounted volume).

Use `owner_id` to scope results to a specific uploader without duplicating routes:
```bash
curl "http://localhost:8000/videos/?owner_id=1&limit=5"
```

---

## Searching Videos

`GET /videos/search` performs semantic search across processed transcripts:
- `q` — required free-text query
- `k` — optional integer (default 5)
- `owner_id` — optional filter applied *after* FAISS scoring

Example:
```bash
curl "http://localhost:8000/videos/search?q=transformers&k=3&owner_id=1"
```
If the FAISS index is empty (no processed videos yet) the endpoint returns `[]`.

---

## How Tests Work

Integration coverage lives in `tests/test_app_integration.py` and exercises the full API surface (health, users, upload/task flow, search, delete) with strategically mocked ML/FAISS calls. Run it inside Docker:
```bash
docker compose run --rm test
```
Pytest is configured with `addopts = tests/test_app_integration.py`, so only that file executes by default. Legacy unit tests remain in the repository for reference.
The production/runtime image stays leaner by leaving pytest/httpx/pytest-asyncio in the dedicated Docker `test` target instead of the main backend/worker image.

---

## Key Directories

```
backend/app/        # FastAPI project (routers, models, services, tasks)
backend/tests/      # pytest suite (integration default)
docs/               # documentation hub (this folder)
media/              # persisted FAISS index + uploads (Docker volume)
```

Refer to:
- `docs/api_reference.md` for endpoint-level details
- `docs/architecture.md` for design notes (async façade, Celery, FAISS)
- `docs/deployment_guide.md` for ops playbooks
