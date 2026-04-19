# AI Knowledge Workspace Processing Service

Repo A on this branch is an internal processing service for the integrated product.

- Repo B: product-facing backend and system of record
- Repo FE: product UI
- Repo A: upload, process, poll status, fetch transcript

This branch intentionally removes product/demo/search responsibilities from Repo A.

## Active responsibility

- accept media uploads from Repo B
- enqueue and run transcription processing
- persist processing state and transcript rows
- return transcript results through the existing processing-side contract

## Not part of this branch

- no semantic or vector search runtime
- no FAISS or embedding pipeline
- no frontend/demo app
- no auth or user-management API
- no ownership/domain logic beyond legacy compatibility fields already accepted by the upload contract

## Active HTTP surface

- `GET /`
- `GET /health`
- `POST /videos/upload`
- `GET /videos/tasks/{task_id}`
- `GET /videos/{video_id}`
- `GET /videos/{video_id}/transcript`

## Runtime services

- `backend`: FastAPI API for upload/status/transcript endpoints
- `worker`: Celery worker for audio extraction, Whisper transcription, and transcript persistence
- `db`: PostgreSQL for durable processing state and transcript rows
- `redis`: Celery broker/result backend
- `test`: isolated test runner profile

Media files are stored under `backend/media/` by default in this branch.

## Quickstart

Run the processing stack:

```bash
docker compose up --build backend worker db redis
```

Run tests:

```bash
docker compose run --rm test
```

## Compatibility notes

- `POST /videos/upload` still returns `{task_id, status, video_id}`.
- `GET /videos/tasks/{task_id}` still mirrors Celery task state.
- `GET /videos/{video_id}/transcript` still returns ordered transcript rows by `segment_index`.
- `owner_id` is still accepted on upload and returned on video reads for backward compatibility, but Repo A does not treat it as an authorization boundary.

## Documentation

- [docs/INDEX.md](./docs/INDEX.md)
- [docs/api_reference.md](./docs/api_reference.md)
- [docs/architecture.md](./docs/architecture.md)
- [docs/deployment_guide.md](./docs/deployment_guide.md)
- [docs/transcript_chunking.md](./docs/transcript_chunking.md)
