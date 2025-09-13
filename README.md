## DemoFastAPI

### 1. Overview
FastAPI backend integrating PostgreSQL, Redis, Celery, Whisper transcription, sentence-transformer embeddings, and FAISS for semantic search. Video uploads trigger asynchronous processing (audio extraction → transcription → segmentation → embedding → FAISS index update). A lightweight in-memory stack (SQLite + in‑memory Celery) powers the dedicated test service.

### 2. Features
- User management (CRUD)
- Video upload endpoint with background Celery task
- FFMPEG audio extraction
- Whisper transcription (worker only; mocked in tests)
- Transcript segmentation + persistence
- Embedding generation (sentence-transformers) + FAISS index storage
- Semantic search endpoint grouping results per video
- Task status polling (`/videos/tasks/{task_id}`)

### 3. Architecture / Services
| Service | Purpose |
|---------|---------|
| backend | FastAPI app (REST + search + enqueue tasks) |
| worker  | Celery worker (transcoding, transcription, embeddings, FAISS) |
| db      | PostgreSQL (runtime persistence) |
| redis   | Celery broker/result backend (prod/dev) |
| test    | Ephemeral test runner (SQLite + memory broker) |

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
    services/              # video_processing.py, semantic_index.py
    tasks/                 # video_tasks.py (process_video)
    schemas/               # Pydantic response/request models
    config/settings.py     # Environment-driven settings
    utils.py               # Helpers (transcript splitting, etc.)
tests/                     # Pytest suite (see below)
```

### 5. Development Setup (Docker Compose)
Create a `.env` (see `.env.example`) then:
```bash
docker compose up --build
```
Access:
```
Swagger: http://localhost:8000/docs
ReDoc:   http://localhost:8000/redoc
Health:  http://localhost:8000/health
```
Stop services:
```bash
docker compose down
```

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

---
Concise, production‑leaning FastAPI skeleton with async processing + semantic search—ready to extend.
