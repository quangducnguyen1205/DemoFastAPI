## DemoFastAPI

### 1. Overview
FastAPI backend integrating PostgreSQL, Redis, Celery, Whisper transcription, sentence-transformer embeddings, and FAISS for semantic search. Video uploads trigger asynchronous processing (audio extraction → transcription → segmentation → embedding → FAISS index update). A lightweight in-memory stack (SQLite + in‑memory Celery) powers the dedicated test service.

### 2. Features
- User management (CRUD)
- Video upload endpoint with background Celery task
- FFMPEG audio extraction
- Whisper transcription (worker only; model reused per worker process and mocked in tests)
- Transcript segmentation + persistence
- Batched embedding generation (sentence-transformers) + FAISS index storage
- Semantic search endpoint grouping results per video
- Task status polling (`/videos/tasks/{task_id}`)

### 3. Architecture / Services
| Service | Purpose |
|---------|---------|
| backend | FastAPI app (REST + search + enqueue tasks) |
| worker  | Celery worker (transcoding, transcription, embeddings, FAISS) using the same Python app image as `backend` |
| db      | PostgreSQL (runtime persistence) |
| redis   | Celery broker/result backend (prod/dev) |
| test    | Ephemeral test runner (SQLite + memory broker) built from the dedicated `test` Docker target |

Media + FAISS artifacts live under `backend/app/media` (mounted into both backend & worker). Test service uses an isolated path inside its container.

### 4. Project Layout (key parts)
```
backend/
  app/
    main.py                # FastAPI app + lifespan creates tables
    core/
      database.py          # Engine (handles in‑memory SQLite with StaticPool)
      celery_app.py        # Central Celery instance
    models/                # user.py, video.py, transcript.py
    routers/               # users.py, videos.py
    services/              # video_processing.py, semantic_index/ helpers
    tasks/                 # video_tasks.py (process_video)
    schemas/               # Pydantic response/request models
    config/settings.py     # Environment-driven settings
    utils.py               # Helpers (transcript splitting, etc.)
tests/                     # Pytest suite (see below)
```

### 11. Quick Commands
Build & run stack:
```bash
docker compose up --build
```
Run only tests:
```bash
docker compose run --rm test
```
Rebuild test image (e.g., after dependency change):
```bash
docker compose build test
```
Use an alternate env file when the default `.env` is unavailable:
```bash
APP_ENV_FILE=.env.example docker compose build backend
```

---

## 📚 Complete Documentation

For comprehensive documentation suitable for academic submission (Project 1), see the **`docs/`** folder:

- **[docs/INDEX.md](./docs/INDEX.md)** — Documentation navigation guide
- **[docs/README.md](./docs/README.md)** — Detailed project overview and problem statement
- **[docs/architecture.md](./docs/architecture.md)** — Technical system design and data flow
- **[docs/api_reference.md](./docs/api_reference.md)** — Complete API endpoint documentation
- **[docs/deployment_guide.md](./docs/deployment_guide.md)** — Step-by-step deployment and troubleshooting
- **[docs/future_work.md](./docs/future_work.md)** — Roadmap and research directions

**Total Documentation:** 3,000+ lines covering all aspects of the system.

---
Concise, production‑leaning FastAPI skeleton with async processing + semantic search—ready to extend.

### 6. API Highlights
- `POST /videos/upload` → returns `{task_id, status, video_id}` and immediately schedules processing.
- `GET /videos/tasks/{task_id}` → check Celery task state.
- `GET /videos/search?q=...` → semantic transcript search (deduplicated by video, similarity sorted).
- Standard CRUD under `/users` and `/videos`.

### 7. Testing
Dedicated `test` service runs the suite with:
- SQLite in‑memory DB (`sqlite+pysqlite:///:memory:`) + SQLAlchemy StaticPool so tables persist across sessions
- Celery memory broker/result backend (no Redis needed)
- ML + FAISS heavy operations mocked in specific tests (search, upload) for speed
- Runtime images avoid installing pytest/httpx/pytest-asyncio; those stay in the `test` target only

Run tests:
```bash
docker compose run --rm test
```
Or a single test file:
```bash
docker compose run --rm test pytest tests/test_users.py -q
```

### 8. Current Test Suite
- Smoke / health: root + `/health`
- Users CRUD: create, read, list, update, delete
- Video upload: mocks task dispatch, validates DB insert & response shape
- Search tests: monkeypatch FAISS + embeddings to validate grouping & similarity ordering
- Celery task stub: ensures task failure path or ready transition logic (uses in‑memory broker)

### 9. Implementation Notes
- `app/main.py` lifespan ensures tables are created at startup (includes model imports first).
- `app/core/database.py` adapts engine for in‑memory SQLite (StaticPool + `check_same_thread=False`).
- FAISS index + mapping persisted in `media/`; tests isolate by using a temporary media root path.
- Uploads are streamed to disk instead of buffering the full file into memory first.
- Worker hot-path logs include lightweight timings for upload, ffmpeg, Whisper, chunking, transcript persistence, embeddings, FAISS writes, and total task time.
- If existing FAISS index/mapping files on the shared media mount are unreadable placeholder artifacts, the runtime recreates them instead of failing every new task.
- Docker Compose now runs the Celery worker conservatively by default (`concurrency=1`, prefetch multiplier `1`) to avoid ML-related memory spikes.
- Backend and worker now reuse the same heavy Python app image, while the Docker `test` target layers test-only dependencies on top separately.
- `backend/.dockerignore` keeps media outputs, caches, and local database artifacts out of the Python build context.
- Local Docker source mounts now target `/backend`, matching the image `WORKDIR`, so backend and worker pick up the mounted code path consistently.

---
Concise, production‑leaning FastAPI skeleton with async processing + semantic search—ready to extend.
