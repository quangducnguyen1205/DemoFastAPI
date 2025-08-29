# DemoFastAPI (Dockerized: backend + worker + redis + postgres)

A FastAPI backend with a Celery worker for background processing. The entire stack runs in Docker using docker-compose.

- backend: FastAPI API server (uvicorn)
- worker: Celery worker for transcription + embeddings
- redis: broker/result backend for Celery
- db: PostgreSQL database

Uploads and artifacts are stored in the container at `/app/media`. Both backend and worker share the same volume, so they can read and write the same files.

## Project structure (high level)

```
DemoFirstBackend/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          # FastAPI app + routers
│   │   ├── database.py      # SQLAlchemy engine/session
│   │   ├── models.py        # ORM (User, Video, Transcript) — consider splitting into app/models/
│   │   ├── schemas.py       # Pydantic models — consider splitting into app/schemas/
│   │   ├── celery.py        # Celery app config
│   │   ├── tasks.py         # Celery tasks (transcription + FAISS) — consider app/tasks/
│   │   └── routers/
│   │       ├── users.py
│   │       └── videos.py    # upload/search/delete endpoints
│   │   ├── utils.py         # shared helpers (e.g., transcript splitting)
│   │   ├── config/
│   │   │   └── settings.py  # centralized env + path config
### Suggested scalable layout (future refactor)

```
app/
  core/               # app-wide init: database, celery, logging, exceptions
  config/             # settings.py (Pydantic BaseSettings or similar)
  models/             # user.py, video.py, transcript.py
  schemas/            # user.py, video.py, transcript.py
  services/           # video_processing.py, transcripts.py, search.py
  tasks/              # celery tasks grouped by domain
  routers/            # users.py, videos.py, tasks.py (status)
  utils/              # small helpers
```

Benefits: clearer ownership, easier testing, smaller modules, simpler imports, and better scalability when adding domains.
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
├── .env
└── README.md
```

## Build and run (Docker Compose)

```bash
# Build images and start all services
docker compose up --build
```

This starts: postgres (db), redis, backend (FastAPI), and worker (Celery).

- API Docs (Swagger): http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

Stop all services:
```bash
docker compose down
```

## What each service does

- backend
  - Hosts the FastAPI API
  - Saves uploaded videos to `/app/media/videos`
  - Enqueues background jobs (transcribe + embeddings) to Celery via Redis

- worker
  - Consumes Celery tasks from Redis
  - Uses ffmpeg to extract audio from uploaded videos
  - Uses Whisper to transcribe audio to text
  - Splits transcript into segments, embeds with Sentence-Transformers, and indexes in FAISS
  - Persists transcripts and FAISS files under `/app/media`

- shared media
  - Both backend and worker mount the project `backend/` folder to `/app`
  - All media artifacts live under `/app/media` so both can access them

## APIs (quick reference)

- GET `/` – root info
- GET `/health` – health check
- Users CRUD under `/users`
- Videos
  - POST `/videos/upload` – upload a file and enqueue a processing task
    - Response: `{ "task_id": "...", "status": "processing", "video_id": <id> }`
  - GET `/videos/tasks/{task_id}` – check Celery task status and results
  - GET `/videos/search?q=...` – semantic search over transcripts (FAISS)
  - Standard CRUD: `/videos/`, `/videos/{id}` (get, put, delete)

Swagger UI: http://localhost:8000/docs

## Notes

- First run may download ML models (Whisper and Sentence-Transformers), which can take a while.
- Consider Alembic for DB migrations if evolving schema in production.
