# Deployment Guide

A practical guide to running the Video Similarity Search stack with Docker Compose and validating that the end-to-end workflow (upload → background processing → search) operates as expected.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Environment Configuration](#environment-configuration)
3. [Docker Compose Deployment](#docker-compose-deployment)
4. [Testing the Stack](#testing-the-stack)
5. [Managing Services](#managing-services)
6. [Troubleshooting](#troubleshooting)
7. [Production Notes](#production-notes)

---

## Prerequisites

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| CPU | 2 cores | Whisper benefits from more cores. |
| RAM | 4 GB | 8 GB recommended for smoother ML workloads. |
| Disk | 15 GB | Stores Postgres data, uploads, FAISS index. |
| OS | Linux / macOS / Windows (WSL2) | Docker Desktop works on macOS/Windows. |
| Software | Docker 20.10+, Docker Compose v2+, Git | `docker compose version` should report v2 or later. |

Install Docker + Compose per your platform (Docker Desktop includes both). Add your user to the `docker` group on Linux if you want to avoid `sudo`.

---

## Environment Configuration

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd DemoFirstBackend
   ```

2. **Create `.env`**
   ```bash
   cp .env.example .env
   ```

3. **Adjust only if needed**
   - `DATABASE_URL=postgresql://postgres:postgres@db:5432/userdb`
   - `CELERY_BROKER_URL=redis://redis:6379/0`
   - `MEDIA_ROOT=/app/media` (mounted volume)
   - `FAISS_INDEX_PATH=/app/media/faiss_index.faiss`
   - `FAISS_MAPPING_PATH=/app/media/faiss_mapping.pkl`

The defaults match the compose service names (`db`, `redis`, `backend`, `worker`).

---

## Docker Compose Deployment

1. **Build the images**
   ```bash
   docker compose build
   ```

2. **Start the stack**
   ```bash
   docker compose up -d
   ```

   Services started:
   - `db` (PostgreSQL 15) — persists metadata
   - `redis` — Celery broker/result backend
   - `backend` — FastAPI app (port 8000)
   - `worker` — Celery worker for Whisper/FAISS pipeline

3. **Observe logs**
   ```bash
   docker compose logs -f backend
   docker compose logs -f worker
   ```

4. **Smoke test**
   ```bash
   curl http://localhost:8000/health
   # -> {"status": "healthy"}
   ```

5. **Upload a sample video**
   ```bash
   curl -X POST http://localhost:8000/videos/upload \
     -F "file=@sample.mp4" \
     -F "title=Sample" \
     -F "owner_id=1"
   ```
   Save the `task_id` and `video_id` from the response.

6. **Track background progress**
   ```bash
   curl http://localhost:8000/videos/tasks/<task_id>
   docker compose logs -f worker
   ```

7. **Search once processing completes**
   ```bash
   curl "http://localhost:8000/videos/search?q=sample&owner_id=1"
   ```

---

## Testing the Stack

The `test` service uses the same Docker image but runs pytest against a lightweight SQLite database and in-memory Celery configuration. Only the integration suite (`tests/test_app_integration.py`) executes by default via `pytest.ini`.

```bash
docker compose run --rm test
```

The tests cover:
- `GET /` and `GET /health`
- User create/read
- Video upload + mocked background task
- Task status polling
- FAISS-backed search with patched reader
- Delete cleanup flow

Legacy unit tests remain in `backend/tests/` but are not part of the default command.

---

## Managing Services

| Action | Command |
|--------|---------|
| Stop stack | `docker compose down` |
| Stop & keep volumes | `docker compose stop` |
| Rebuild backend only | `docker compose build backend && docker compose up -d backend` |
| Restart worker | `docker compose restart worker` |
| Scale workers | `docker compose up -d --scale worker=3` |
| Tail service logs | `docker compose logs -f <service>` |

Use `docker compose down -v` to wipe Postgres/Redis/FAISS volumes when you need a clean slate.

---

## Troubleshooting

### FastAPI container restarts immediately
- Run `docker compose logs backend` to view stack traces.
- Common cause is Postgres not ready yet; the app retries table creation up to 30 times.

### Tasks stay PENDING
- Ensure the worker is running: `docker compose ps worker`.
- Check Redis connectivity (worker logs will show connection errors).
- Whisper model download failures also surface in worker logs.

### Upload succeeds but file missing
- Confirm the `media` volume exists: `docker volume ls | grep media`.
- Ensure the path you POSTed is valid and not zero bytes.

### Search returns empty results
- Video may still be processing; check `/videos/tasks/{task_id}` and the `videos.status` column.
- Verify FAISS files exist inside the media volume (`faiss_index.faiss`, `faiss_mapping.pkl`).

### Port conflicts
- Change host ports inside `docker-compose.yml`, e.g. `- "8001:8000"` under `backend`.

---

## Production Notes

- Replace default credentials and consider managed Postgres/Redis services.
- Mount `MEDIA_ROOT` on durable block storage or S3-compatible volumes if you need persistence beyond a single host.
- Configure HTTPS termination (e.g., Traefik or Nginx) in front of FastAPI.
- Run multiple `backend` containers behind a load balancer; FastAPI is stateless.
- Scale Celery workers separately for throughput; Whisper and embedding models are CPU-heavy.
- Schedule periodic FAISS backups by copying the index and mapping files from the media volume.
- Monitor queue depth (Redis), worker logs, and API latencies. Prometheus exporters can be added later.

For additional architectural context and API specifics, read `docs/architecture.md` and `docs/api_reference.md` respectively.
