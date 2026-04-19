# Deployment Guide

This guide covers the processing-only branch of Repo A.

## Docker Compose services

- `backend`
- `worker`
- `db`
- `redis`
- `test` (profile only)

## Start the processing stack

```bash
docker compose up --build backend worker db redis
```

## Important environment values

- `DATABASE_URL`
- `MEDIA_ROOT`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CELERY_WORKER_PREFETCH_MULTIPLIER`

Current Compose defaults align media storage at `/backend/media` inside the backend and worker containers.

## Basic verification

1. Check health:

```bash
curl http://localhost:8000/health
```

2. Upload a video:

```bash
curl -X POST http://localhost:8000/videos/upload \
  -F "file=@sample.mp4" \
  -F "title=Sample Lecture"
```

3. Poll task status:

```bash
curl http://localhost:8000/videos/tasks/<task_id>
```

4. Fetch transcript:

```bash
curl http://localhost:8000/videos/<video_id>/transcript
```

## Run tests

```bash
docker compose run --rm test
```

## Branch-specific note

This branch is not meant to run a frontend or a search stack. If you are looking for product-facing behavior, use Repo B and Repo FE.
