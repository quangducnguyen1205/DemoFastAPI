# Repo A Branch Overview

Repo A on this branch is an internal processing service used by the current integrated product.

## Service boundary

- Repo B owns product-facing APIs, authorization, search behavior, and workspace/domain logic.
- Repo FE owns the product UI.
- Repo A owns media intake, background processing, processing-state persistence, and transcript delivery.

## Processing flow

1. Repo B uploads media through `POST /videos/upload`.
2. Repo A stores the file locally and creates a `videos` row with `status="processing"`.
3. Repo A enqueues `process_video_task(video_id, abs_video_path)` through Celery.
4. The worker extracts audio, runs Whisper, chunks transcript text, persists `transcripts` rows, and updates `videos.status`.
5. Repo B polls `GET /videos/tasks/{task_id}` and can read `GET /videos/{video_id}` or `GET /videos/{video_id}/transcript`.

## Persistence boundary

Repo A still uses PostgreSQL because durable processing state matters for:

- upload-to-job linkage
- processing status
- transcript row retrieval after processing completes

Repo A does not act as the product system of record.

## Legacy compatibility

- `owner_id` is still accepted on upload and returned on video reads if Repo B already sends it.
- Search endpoints, FAISS state, user routes, and frontend code are removed from the active branch.

## Current docs

- [api_reference.md](./api_reference.md)
- [architecture.md](./architecture.md)
- [deployment_guide.md](./deployment_guide.md)
- [transcript_chunking.md](./transcript_chunking.md)
