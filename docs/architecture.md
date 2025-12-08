# System Architecture

This document explains how the FastAPI backend, Celery worker, and FAISS search layer collaborate to deliver the cleaned API surface (root/health, minimal user endpoints, and the video/upload/search flow).

## Table of Contents
1. [High-Level View](#high-level-view)
2. [Core Components](#core-components)
3. [Async Façade Pattern](#async-façade-pattern)
4. [Data Flows](#data-flows)
5. [Celery + FAISS Integration](#celery--faiss-integration)
6. [Database & Ownership Filtering](#database--ownership-filtering)
7. [Scalability Considerations](#scalability-considerations)

---

## High-Level View

```
HTTP Client --> FastAPI Routers (users, videos)
                    |    |  (async functions)
                    |    +--> run_in_threadpool (SQLAlchemy, filesystem)
                    |          |
                    |          +--> PostgreSQL (metadata)
                    |          +--> Media volume (uploads)
                    |
                    +--> Celery Broker (Redis)
                               |
                               +--> Celery Worker (Whisper, embeddings, FAISS writer)
                                             |
                                             +--> FAISS index + mapping (shared volume)
                                             +--> PostgreSQL (status + transcripts)

Search Endpoint --> FAISS Reader (load_index_if_exists + search_vector)
```

The API layer stays lean: GET `/`, GET `/health`, POST `/users/`, GET `/users/{id}`, and the video endpoints (`/videos/upload`, `/videos/tasks/{task_id}`, `/videos/`, `/videos/{id}`, `DELETE /videos/{id}`, `/videos/search`).

---

## Core Components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| FastAPI app | `app/main.py` | Bootstraps the application, registers routers, exposes health + root endpoints, and keeps startup idempotent via the lifespan hook. |
| Users router | `app/routers/users.py` | Minimal create + read handlers for associating uploads with owners. |
| Videos router | `app/routers/videos.py` | Handles uploads, listing/filtering, retrieval, deletion, search, and task-status polling. |
| Database layer | `app/core/database.py` | SQLAlchemy engine/session factory and dependency provider (`get_db`). |
| Celery app | `app/core/celery_app.py` + `app/tasks/video_tasks.py` | Runs the heavy Whisper/embedding/FAISS pipeline for each upload. |
| Semantic services | `app/services/semantic_index/*` | Generate embeddings, load FAISS index, perform searches. |
| Tests | `backend/tests/test_app_integration.py` | Exercises the public API surface with patched ML components. |

---

## Async Façade Pattern

Routers are declared `async`, but the underlying logic (SQLAlchemy ORM calls, filesystem access, Celery client) is synchronous. Each handler wraps the synchronous function in `run_in_threadpool`:

```python
def _sync_list_videos():
    query = db.query(models.Video)
    if owner_id is not None:
        query = query.filter(models.Video.owner_id == owner_id)
    return query.offset(skip).limit(limit).all()

return await run_in_threadpool(_sync_list_videos)
```

Benefits:
- Keeps FastAPI's event loop non-blocking.
- Avoids rewriting all dependencies to be async-aware.
- Threadpool boundaries make mocking easier in tests.

---

## Data Flows

### Upload + Processing

1. Client submits multipart request to `POST /videos/upload`.
2. Endpoint writes the binary into `MEDIA_ROOT/videos/` (UUID file name) and inserts a `Video` row with `status="processing"` + optional `owner_id`.
3. Celery task `process_video_task.delay(video_id, abs_path)` queues work through Redis.
4. Worker pipeline:
   - Extract audio via ffmpeg.
   - Transcribe with Whisper.
   - Segment transcript text.
   - Persist transcript rows in PostgreSQL.
   - Generate embeddings with sentence-transformers.
   - Update FAISS index (`faiss_index.faiss`) and mapping (`faiss_mapping.pkl`).
   - Set the video `status` to `ready` or `failed`.

Clients poll `GET /videos/tasks/{task_id}` and optionally fetch the updated video record once status flips to `ready`.

### Listing + Detail + Delete

- `GET /videos/?owner_id=<int>` reads directly from SQLAlchemy; no Celery involvement.
- `GET /videos/{id}` and `DELETE /videos/{id}` operate on the same table. Delete attempts to remove the corresponding file but guards against path traversal by checking the resolved path against the configured video directory.

### Semantic Search

1. FastAPI handler validates `q`, `k`, and optional `owner_id`.
2. Query embedding is generated (sentence-transformer) inside `_sync_search`.
3. `load_index_if_exists(dim)` returns a cached FAISS `IndexFlatL2` (or empty index if no file yet).
4. `search_vector` runs `index.search` for more segments than videos (k×4, minimum 50) to allow deduplication.
5. The pickled mapping (`faiss_mapping.pkl`) translates FAISS row IDs back to `video_id` values.
6. For each candidate video, the handler fetches metadata from PostgreSQL, converts L2 distances to bounded similarity scores, filters by `owner_id` when provided, sorts, and returns the top `k` `VideoSearchResult` models.

---

## Celery & FAISS Integration

| Stage | Responsibility | Notes |
|-------|----------------|-------|
| Celery broker | Redis | Transports messages between API uploads and workers. |
| Celery worker | `process_video_task` | Includes error handling and DB session management; failures mark the video as `failed` and bubble a short message back to `/videos/tasks/{task_id}`. |
| FAISS writer | `app/services/semantic_index/writer.py` (invoked from the worker) | Adds new vectors, persists both index and mapping synchronously to avoid corruption. |
| FAISS reader | `app/services/semantic_index/reader.py` (used by FastAPI) | Lazily loads the index, caches it in-memory, and only performs read/search operations. |

Because writes happen only inside the worker, the API server can safely cache the FAISS index without dealing with concurrent writes. If a worker updates the files, the process can be restarted (or cache invalidated manually) to pick up the latest vectors.

---

## Database & Ownership Filtering

### Tables

- `users`
  - `id`, `name`, `email`
  - Simple table used for associating uploads with an owner.
- `videos`
  - `id`, `title`, `description`, `url`, `path`, `owner_id`, `status`, timestamps.
  - `owner_id` is nullable; routes now support filtering by this column.
- `transcripts` (not exposed through public APIs yet)
  - Stores transcript text plus metadata used by FAISS updates.

### Owner Filters

- `GET /videos/` applies `Video.owner_id == owner_id` whenever the query param is supplied.
- `GET /videos/search` filters **after** the FAISS ranking step; if `owner_id` is provided, any fetched `Video` whose `owner_id` does not match is discarded before the final top-`k` slice.

This approach keeps the FAISS index global (single vector space) but lets clients scope results to their own uploads without duplicating indices.

---

## Scalability Considerations

- **API layer**: stateless; scale horizontally by running multiple `backend` containers behind a load balancer.
- **Threadpool size**: FastAPI inherits the default `anyio.to_thread` pool; tune via Uvicorn settings if uploads compete with heavy synchronous queries.
- **Worker count**: scale `worker` services independently (each requires Whisper + embedding model downloads). CPU is the main bottleneck.
- **Storage**: mount `MEDIA_ROOT` on persistent storage. The FAISS index and mapping live next to the uploads so snapshots/backups capture both.
- **Cache invalidation**: restart FastAPI containers after major FAISS updates to reload the cached index, or extend the reader to detect newer timestamps.
- **Future auth**: planned enhancements include authentication/authorization layers and admin-only routers (see `docs/future_work.md`).

For implementation specifics, inspect:
- `app/routers/videos.py` for async façade patterns and owner filtering.
- `app/tasks/video_tasks.py` for Celery orchestration.
- `app/services/semantic_index/reader.py` for FAISS search mechanics.
