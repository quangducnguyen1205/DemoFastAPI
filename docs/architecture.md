# Processing-Service Architecture

## Role in the integrated system

Repo A is the internal processing service.

- Repo B calls Repo A for upload, task polling, and transcript retrieval.
- Repo B remains the product-facing backend and search owner.
- Repo FE remains the UI.

## Runtime components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| FastAPI API | `backend/app/main.py` | Exposes the processing endpoints and health/docs surface. |
| Videos router | `backend/app/routers/videos.py` | Upload, task polling, single-video status lookup, and transcript retrieval. |
| Celery app | `backend/app/core/celery_app.py` | Queue orchestration for background processing. |
| Worker task | `backend/app/tasks/video_tasks.py` | Extract audio, transcribe, chunk transcript text, persist transcripts, and update status. |
| Processing helpers | `backend/app/services/video_processing.py` | ffmpeg extraction, Whisper access, transcript chunking, and transcript persistence. |
| Persistence | `backend/app/models/video.py`, `backend/app/models/transcript.py` | Durable processing state and transcript rows. |

## End-to-end flow

1. `POST /videos/upload` stores the file under `MEDIA_ROOT/videos/`.
2. The API inserts a `videos` row and sets `status="processing"`.
3. The API enqueues `process_video_task(video_id, abs_video_path)`.
4. The worker:
   - extracts mono WAV audio via ffmpeg
   - transcribes audio with Whisper
   - chunks transcript text
   - persists transcript rows in PostgreSQL
   - updates `videos.status` to `ready` or `failed`
5. Repo B polls `GET /videos/tasks/{task_id}` and can fetch `GET /videos/{video_id}` or `GET /videos/{video_id}/transcript`.

## Persistence boundary

Repo A intentionally keeps only processing-oriented state:

- `videos`
  - upload metadata
  - storage path
  - durable processing status
  - optional legacy `owner_id` passthrough
- `transcripts`
  - ordered transcript rows by `segment_index`

Repo A does not own product auth, user identity, search indexes, or workspace/business logic in this branch.

## Removed from active runtime

- semantic/vector search
- FAISS index files and mapping management
- embedding generation
- auth and user CRUD
- frontend/demo app
