# FastAPI Current Capabilities and Gaps

This memo is based on code inspection of the current FastAPI repository, with code treated as the source of truth over repo docs. The purpose is to capture confirmed current behavior that a separate Spring Boot system may need to preserve, replace, or explicitly redefine.

## 1. Confirmed current endpoints

| Method | Path | Confirmed behavior | Evidence |
| --- | --- | --- | --- |
| `GET` | `/` | Returns a welcome payload with `message`, `docs`, and `redoc`. | `backend/app/main.py` |
| `GET` | `/health` | Returns `{"status": "healthy"}`. | `backend/app/main.py` |
| `GET` | `/docs` | FastAPI Swagger UI is enabled. | `backend/app/main.py` |
| `GET` | `/redoc` | FastAPI ReDoc is enabled. | `backend/app/main.py` |
| `POST` | `/auth/register` | Registers a user and returns a bearer token plus user payload. | `backend/app/routers/auth.py`, `backend/app/main.py` |
| `POST` | `/auth/login` | Authenticates a user and returns a bearer token plus user payload. | `backend/app/routers/auth.py`, `backend/app/main.py` |
| `POST` | `/users/` | Creates a user record if the email is not already registered. | `backend/app/routers/users.py`, `backend/app/main.py` |
| `GET` | `/users/{user_id}` | Returns a user by ID or `404`. | `backend/app/routers/users.py`, `backend/app/main.py` |
| `GET` | `/videos/search` | Performs semantic search and returns video-level results, not transcript-chunk results. Response items contain `video_id`, `title`, `path`, and `similarity_score`. | `backend/app/routers/videos.py`, `backend/app/schemas/videos.py` |
| `POST` | `/videos/upload` | Accepts a multipart video file plus `title` and optional `owner_id`, stores the file locally, creates a `Video` row with `status="processing"`, and enqueues a Celery task. | `backend/app/routers/videos.py` |
| `GET` | `/videos/tasks/{task_id}` | Returns Celery task state and, for terminal states, either `result` or `error`. | `backend/app/routers/videos.py` |
| `GET` | `/videos` | Lists videos with optional `owner_id` filter and `skip`/`limit` pagination. | `backend/app/routers/videos.py` |
| `GET` | `/videos/{video_id}` | Returns a single video by ID or `404`. | `backend/app/routers/videos.py` |
| `DELETE` | `/videos/{video_id}` | Deletes the video row and attempts to delete the associated local file if it resolves inside the configured video directory. | `backend/app/routers/videos.py` |
| `GET` | `/videos/{video_id}/transcript` | Returns transcript rows for a video, ordered by `segment_index`. | `backend/app/routers/videos.py`, `backend/app/models/transcript.py`, `backend/app/schemas/transcripts.py` |

## 2. Confirmed current processing flow

1. `POST /videos/upload` rejects non-`video/*` MIME types, writes the uploaded file under `settings.VIDEO_DIR` using a UUID filename, and inserts a `videos` row with `title`, `url`, `path`, optional `owner_id`, and `status="processing"`. It then enqueues `process_video_task(video_id, abs_video_path)` via Celery and returns `task_id`, `status`, and `video_id`.  
Evidence: `backend/app/routers/videos.py`, `backend/app/models/video.py`

2. The background worker loads the `Video` row, extracts mono 16 kHz WAV audio with `ffmpeg`, then calls local Whisper transcription using `whisper.load_model("base")`.  
Evidence: `backend/app/tasks/video_tasks.py`, `backend/app/services/video_processing.py`, `backend/Dockerfile`

3. If transcription returns text, that text is split into sentence-aware chunks with a default ceiling of 450 characters. The splitter groups sentence-like fragments ending in `.`, `!`, or `?`, reuses the last sentence as overlap when it still fits, and wraps overlong fragments on word boundaries instead of blind character slicing.  
Evidence: `backend/app/tasks/video_tasks.py`, `backend/app/services/video_processing.py`, `backend/app/utils.py`

4. Transcript chunks are persisted as `Transcript` rows with `video_id`, sequential `segment_index` values starting at `0`, and `text`. No timestamp fields, speaker fields, or chunk IDs are written by this code path.  
Evidence: `backend/app/services/video_processing.py`, `backend/app/models/transcript.py`

5. For each transcript chunk, the worker generates an embedding with `SentenceTransformer("all-MiniLM-L6-v2")`, adds the vectors to an on-disk FAISS `IndexFlatL2`, and writes a pickle mapping from FAISS row ID to `video_id`.  
Evidence: `backend/app/tasks/video_tasks.py`, `backend/app/services/semantic_index/__init__.py`, `backend/app/services/semantic_index/writer.py`

6. On the happy path, the worker sets `video.status = "ready"` and returns `{"status": "ready", "segments": segments}`. On an exception inside the worker try-block, it sets `video.status = "failed"` and returns `{"status": "failed", "error": ...}`.  
Evidence: `backend/app/tasks/video_tasks.py`

7. `GET /videos/tasks/{task_id}` surfaces Celery state separately from the `video.status` field. `SUCCESS` returns the task result payload; `FAILURE` returns the exception string.  
Evidence: `backend/app/routers/videos.py`

8. `GET /videos/search` embeds the query text with the same sentence-transformer model, searches FAISS by L2 distance, converts each distance to `1 / (1 + distance)`, maps FAISS hits back to `video_id`, keeps the best score per video, optionally filters by `owner_id`, and returns the top `k` videos. The API response is video-level, even though the underlying index is built from transcript segments.  
Evidence: `backend/app/routers/videos.py`, `backend/app/services/semantic_index/__init__.py`, `backend/app/services/semantic_index/reader.py`, `backend/app/services/semantic_index/writer.py`

## 3. Confirmed current data fields for video and transcript

### `videos` table

| Field | Confirmed definition | Evidence |
| --- | --- | --- |
| `id` | Integer primary key, indexed | `backend/app/models/video.py` |
| `title` | `String(255)`, required | `backend/app/models/video.py` |
| `description` | `Text`, nullable | `backend/app/models/video.py` |
| `url` | `String(500)`, required | `backend/app/models/video.py` |
| `path` | `String(500)`, nullable, indexed | `backend/app/models/video.py` |
| `owner_id` | Integer foreign key to `users.id`, nullable, indexed | `backend/app/models/video.py` |
| `status` | `String(50)`, nullable | `backend/app/models/video.py` |
| `created_at` | Timestamp with timezone, server default `now()` | `backend/app/models/video.py` |
| `updated_at` | Timestamp with timezone, updated on change | `backend/app/models/video.py` |

Confirmed API-facing video read fields are `id`, `title`, `description`, `url`, `path`, `owner_id`, `status`, `created_at`, and `updated_at`.  
Evidence: `backend/app/schemas/videos.py`

### `transcripts` table

| Field | Confirmed definition | Evidence |
| --- | --- | --- |
| `id` | Integer primary key, indexed | `backend/app/models/transcript.py` |
| `video_id` | Integer foreign key to `videos.id`, `ondelete="CASCADE"`, required, indexed | `backend/app/models/transcript.py` |
| `segment_index` | Integer, required, indexed | `backend/app/models/transcript.py` |
| `text` | `Text`, required | `backend/app/models/transcript.py` |
| `created_at` | Timestamp with timezone, server default `now()` | `backend/app/models/transcript.py` |

There is also a unique constraint on `(video_id, segment_index)`. Confirmed API-facing transcript read fields are `id`, `video_id`, `segment_index`, `text`, and `created_at`.  
Evidence: `backend/app/models/transcript.py`, `backend/app/schemas/transcripts.py`

### Important non-fields

The current code does **not** define transcript start/end timestamps, speaker labels, word-level timing, chunk IDs, artifact URIs, or any transcript confidence fields.  
Evidence: `backend/app/models/transcript.py`, `backend/app/schemas/transcripts.py`, `backend/app/services/video_processing.py`

## 4. Confirmed runtime/services and local-dev implications

| Area | Confirmed current setup | Evidence |
| --- | --- | --- |
| API runtime | FastAPI app served by `uvicorn app.main:app` on port `8000` | `docker-compose.yml`, `backend/Dockerfile` |
| Background work | Separate Celery worker process running `app.tasks.video_tasks` | `docker-compose.yml`, `backend/app/core/celery_app.py` |
| Database | PostgreSQL 15 container on port `5432`; app uses SQLAlchemy | `docker-compose.yml`, `backend/app/core/database.py` |
| Queue/result backend | Redis 7 container on port `6379`; Celery broker and result backend both point to Redis by default | `docker-compose.yml`, `backend/app/config/settings.py`, `backend/app/core/celery_app.py` |
| Media storage | Uploaded videos and FAISS files are stored on the local filesystem under paths derived from `MEDIA_ROOT` | `backend/app/config/settings.py`, `backend/app/routers/videos.py`, `backend/app/services/semantic_index/writer.py` |
| Audio extraction | `ffmpeg` is required in the runtime image | `backend/Dockerfile`, `backend/app/services/video_processing.py` |
| ML libraries | Python requirements include `openai-whisper`, `sentence-transformers`, and `faiss-cpu` | `backend/requirements.txt` |
| Startup behavior | App startup retries DB readiness and then runs `Base.metadata.create_all(bind=engine)` | `backend/app/main.py` |
| CORS | `allow_origins=["*"]`, all methods, all headers, credentials enabled | `backend/app/main.py` |
| Test harness | A `test` compose profile exists with SQLite and in-memory Celery backends | `docker-compose.yml` |

Local-dev implications confirmed by code and compose:

- Upload processing depends on the worker, Redis, the database, and filesystem access to the same media/index paths used by the API. If the worker is not running, uploads will enqueue but not complete.  
Evidence: `backend/app/routers/videos.py`, `backend/app/tasks/video_tasks.py`, `backend/app/core/celery_app.py`

- Search depends on FAISS index files written by the worker and read by the API. Backend and worker therefore need path-aligned `FAISS_INDEX_PATH` and `FAISS_MAPPING_PATH` values.  
Evidence: `backend/app/routers/videos.py`, `backend/app/services/semantic_index/reader.py`, `backend/app/services/semantic_index/writer.py`, `backend/app/config/settings.py`

- The compose file mounts source and media under `/app`, while the image `WORKDIR` is `/backend` and settings default to `media` or `/backend/media` unless overridden by env. This means local-dev behavior depends on `.env` path alignment and should not be assumed to be correct from compose alone.  
Evidence: `docker-compose.yml`, `backend/Dockerfile`, `backend/app/config/settings.py`

- The inspected runtime path relies on SQLAlchemy metadata creation at app startup. A migration workflow is not evidenced in the inspected files reviewed for this memo.  
Evidence: `backend/app/main.py`

## 5. Reusable capabilities for the new system

- The current API contract already separates upload from processing completion: upload creates a durable `Video` row and returns a task handle, while task status is polled independently. That asynchronous contract is reusable even if the implementation moves away from Celery.  
Evidence: `backend/app/routers/videos.py`, `backend/app/tasks/video_tasks.py`

- The legacy system has a concrete, reproducible transcript segmentation rule: sentence-aware grouping with a 450-character ceiling, one-sentence overlap when it fits, and sequential `segment_index`. If the Spring system needs behavioral parity for search or transcript display, this rule can be mirrored directly.  
Evidence: `backend/app/utils.py`, `backend/app/services/video_processing.py`

- The current search behavior is well defined enough to replicate: embed transcript segments, store vectors in FAISS, search by query embedding, convert L2 distance to `1 / (1 + distance)`, then collapse multiple segment hits into one best score per video.  
Evidence: `backend/app/tasks/video_tasks.py`, `backend/app/routers/videos.py`, `backend/app/services/semantic_index/reader.py`, `backend/app/services/semantic_index/writer.py`

- The current persistence model is minimal and portable: `Video` stores basic file/status ownership data, and `Transcript` stores ordered plain-text segments. Those are strong candidates for a compatibility baseline if the new system needs to read or reason about legacy outputs.  
Evidence: `backend/app/models/video.py`, `backend/app/models/transcript.py`

- User registration/login endpoints already exist, but they are separate from the video-processing flow. They are reusable only as reference behavior, not as proof of integrated authorization around video assets.  
Evidence: `backend/app/routers/auth.py`, `backend/app/routers/videos.py`

## 6. Current limitations / mismatches for Spring integration

- Video endpoints are not protected by the auth dependency shown in `backend/app/routers/auth.py`. `owner_id` is accepted as a plain form/query value on upload, list, and search rather than being derived from the authenticated user. That is a likely mismatch if the Spring system will enforce ownership through security context.  
Evidence: `backend/app/routers/videos.py`, `backend/app/routers/auth.py`

- The search API is video-level only. Internally it indexes transcript segments, but the persisted mapping is FAISS row ID -> `video_id`, and the response model exposes no transcript segment reference, transcript text snippet, or transcript row ID.  
Evidence: `backend/app/services/semantic_index/writer.py`, `backend/app/routers/videos.py`, `backend/app/schemas/videos.py`

- Transcript storage is much thinner than many downstream knowledge/search systems expect. There are no timestamps, speaker labels, word timings, artifact links, or structured chunk metadata.  
Evidence: `backend/app/models/transcript.py`, `backend/app/schemas/transcripts.py`

- Processing is not transactionally consistent across transcript persistence, embedding/index writes, and final status updates. `persist_transcript_segments()` commits transcript rows before FAISS writes happen, so later embedding/index failures can leave transcript rows present while `video.status` is `"failed"`.  
Evidence: `backend/app/services/video_processing.py`, `backend/app/tasks/video_tasks.py`

- A Whisper failure inside `transcribe_audio_with_whisper()` is converted to `None`, not raised. In that case the worker produces `segments = []` and still marks the video `"ready"`. That means `"ready"` does not guarantee a non-empty transcript.  
Evidence: `backend/app/services/video_processing.py`, `backend/app/tasks/video_tasks.py`

- Video deletion does not remove FAISS vectors or rewrite the FAISS mapping file. Deleted videos are filtered out at read time because the API re-queries the database, but stale vector/index entries can remain on disk.  
Evidence: `backend/app/routers/videos.py`, `backend/app/services/semantic_index/writer.py`

- Storage is local-filesystem based, and vector state is stored in local FAISS/pickle files shared across processes. That is a very different operational model from a typical Spring Boot service backed by object storage and managed search infrastructure.  
Evidence: `backend/app/config/settings.py`, `backend/app/routers/videos.py`, `backend/app/services/semantic_index/writer.py`

- Status values are free-form strings on the `videos` table rather than an enum or state machine contract. The code currently writes `"processing"`, `"ready"`, and `"failed"`, but the schema does not enforce those values.  
Evidence: `backend/app/models/video.py`, `backend/app/routers/videos.py`, `backend/app/tasks/video_tasks.py`

- The defined `VideoUploadResponse` schema is not the live `/videos/upload` contract. The route currently returns a task-oriented payload with `task_id`, `status`, and `video_id` instead of the metadata-rich schema declared in `backend/app/schemas/videos.py`.  
Evidence: `backend/app/routers/videos.py`, `backend/app/schemas/videos.py`

- The repo contains both `backend/app/services/semantic_index.py` and `backend/app/services/semantic_index/`. That duplicate naming is a maintenance hazard when porting or comparing indexing logic.  
Evidence: `backend/app/services/semantic_index.py`, `backend/app/services/semantic_index/__init__.py`

## 7. Unknowns that must NOT be assumed

- Do not assume transcript rows correspond to time-based media segments. The only confirmed transcript ordering field is `segment_index`, and chunking is still text-based rather than timestamp-based.  
Evidence: `backend/app/models/transcript.py`, `backend/app/utils.py`

- Do not assume the search stack can trace a hit back to a specific transcript row. The confirmed FAISS mapping stores only `video_id` values, not transcript IDs or segment indices.  
Evidence: `backend/app/services/semantic_index/writer.py`

- Do not assume `/videos/search` is a chunk-level product search API. The confirmed response model and grouping logic return one result per video.  
Evidence: `backend/app/routers/videos.py`, `backend/app/schemas/videos.py`

- Do not assume video ownership or authorization is enforced server-side for video operations. The code shown accepts caller-supplied `owner_id` values and does not use the auth dependency in `videos.py`.  
Evidence: `backend/app/routers/videos.py`, `backend/app/routers/auth.py`

- Do not assume deleting a video fully removes all derived artifacts. Transcript row deletion is implied by database/ORM relationships, but FAISS cleanup is not implemented in the inspected delete path.  
Evidence: `backend/app/models/transcript.py`, `backend/app/models/video.py`, `backend/app/routers/videos.py`

- Do not assume local Docker mounts currently line up with runtime media/index paths. That depends on environment overrides not proven by the inspected code alone.  
Evidence: `docker-compose.yml`, `backend/Dockerfile`, `backend/app/config/settings.py`

- Do not assume a migration framework, object storage layer, external vector database, or artifact schema exists elsewhere just because the new system may need one. None of those are confirmed by the inspected files used for this memo.  
Evidence: `backend/app/main.py`, `backend/app/config/settings.py`, `backend/app/services/semantic_index/writer.py`

- Do not assume Spring should preserve every current edge case automatically. In particular, whether `"ready"` should still be allowed with an empty transcript is a product decision, not a safe default.  
Evidence: `backend/app/services/video_processing.py`, `backend/app/tasks/video_tasks.py`

## 8. Recommendation: what can be reused as-is vs what needs clarification

### Reuse as-is when parity matters

- Reuse the current endpoint-level flow of upload -> async processing -> task polling -> transcript retrieval -> video-level semantic search, because that behavior is concrete in code today.  
Evidence: `backend/app/routers/videos.py`, `backend/app/tasks/video_tasks.py`

- Reuse the current transcript chunking rule and video-level search aggregation if the goal is strict behavioral compatibility with the legacy repo. Those rules are explicit and reproducible.  
Evidence: `backend/app/utils.py`, `backend/app/routers/videos.py`, `backend/app/services/semantic_index/reader.py`

- Reuse the current minimal persistence contract for legacy compatibility: `Video` metadata plus ordered plain-text `Transcript` segments.  
Evidence: `backend/app/models/video.py`, `backend/app/models/transcript.py`

### Clarify before implementing in Spring

- Clarify the target authorization model for videos. The current FastAPI code does not enforce authenticated ownership on video operations, and Spring almost certainly should not inherit that implicitly.  
Evidence: `backend/app/routers/videos.py`, `backend/app/routers/auth.py`

- Clarify the target transcript schema. If the new system needs timestamps, speaker attribution, traceable chunk IDs, or richer artifacts, those are net-new requirements and are not present in the current source of truth.  
Evidence: `backend/app/models/transcript.py`, `backend/app/schemas/transcripts.py`

- Clarify the desired success/failure semantics for processing. Today a video can be marked `"ready"` with no transcript, and partial transcript persistence can survive later indexing failures. Spring should preserve or intentionally change that behavior, but not drift accidentally.  
Evidence: `backend/app/services/video_processing.py`, `backend/app/tasks/video_tasks.py`

- Clarify the future storage contract for uploaded media, derived transcripts, and vector indexes. The current implementation relies on local files plus FAISS/pickle state, which is unlikely to be a direct fit for a separate Spring Boot product core.  
Evidence: `backend/app/config/settings.py`, `backend/app/services/semantic_index/writer.py`, `docker-compose.yml`

- Clarify whether the new product needs video-level retrieval only, or true transcript/chunk-level retrieval with traceability back to stored transcript records. The current API does only the former.  
Evidence: `backend/app/routers/videos.py`, `backend/app/services/semantic_index/writer.py`

- Clarify migration strategy before attempting reuse of runtime setup. The current repo auto-creates tables on startup and shows a path mismatch risk between compose mounts and runtime defaults, so the Spring repo should define its own explicit deployment/storage conventions rather than copying Docker settings blindly.  
Evidence: `backend/app/main.py`, `docker-compose.yml`, `backend/Dockerfile`, `backend/app/config/settings.py`
