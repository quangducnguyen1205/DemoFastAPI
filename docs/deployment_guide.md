# Deployment Guide

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Environment Configuration](#environment-configuration)
3. [Docker Compose Deployment](#docker-compose-deployment)
4. [Testing the Deployment](#testing-the-deployment)
5. [Managing Services](#managing-services)
6. [Troubleshooting](#troubleshooting)
7. [Production Considerations](#production-considerations)
8. [Maintenance Tasks](#maintenance-tasks)

---

## Prerequisites

### System Requirements

**Minimum:**
- CPU: 2 cores
- RAM: 4 GB
- Disk: 10 GB free space
- OS: Linux, macOS, or Windows with WSL2

**Recommended:**
- CPU: 4+ cores (for parallel Celery workers)
- RAM: 8 GB (Whisper model loading requires ~2 GB)
- Disk: 50 GB (for video storage and FAISS index)
- SSD for better I/O performance

### Software Dependencies

1. **Docker** (version 20.10 or higher)
   ```bash
   docker --version
   # Expected: Docker version 20.10.x or newer
   ```

2. **Docker Compose** (version 2.0 or higher)
   ```bash
   docker compose version
   # Expected: Docker Compose version v2.x.x
   ```

3. **Git** (for cloning the repository)
   ```bash
   git --version
   ```

### Installation Guides

**Docker on Ubuntu/Debian:**
```bash
# Remove old versions
sudo apt-get remove docker docker-engine docker.io containerd runc

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group (optional, avoids sudo)
sudo usermod -aG docker $USER
newgrp docker
```

**Docker on macOS:**
```bash
# Install Docker Desktop from https://www.docker.com/products/docker-desktop
# Or use Homebrew:
brew install --cask docker
```

**Docker on Windows:**
- Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/
- Ensure WSL2 backend is enabled

---

## Environment Configuration

### 1. Clone the Repository

```bash
git clone <repository-url>
cd DemoFirstBackend
```

### 2. Create Environment File

Copy the example environment file:

```bash
cp .env.example .env
```

### 3. Configure Environment Variables

Edit `.env` with your preferred text editor:

```bash
nano .env
# or
vim .env
```

**Example `.env` file:**

```bash
# Database Configuration
DATABASE_URL=postgresql://postgres:postgres@db:5432/userdb

# Media Storage
MEDIA_ROOT=/app/media
VIDEO_SUBDIR=videos

# FAISS Index Paths (auto-generated if not specified)
FAISS_INDEX_PATH=/app/media/faiss_index.faiss
FAISS_MAPPING_PATH=/app/media/faiss_mapping.pkl

# Celery / Redis
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Optional: Disable tokenizers parallelism warnings
TOKENIZERS_PARALLELISM=false

# Optional: Enable DEBUG mode
DEBUG=false
```

### Variable Descriptions

| Variable | Purpose | Default | Notes |
|----------|---------|---------|-------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@db:5432/userdb` | Change credentials for production |
| `MEDIA_ROOT` | Base directory for uploads | `/app/media` | Must match Docker volume mount |
| `CELERY_BROKER_URL` | Redis message queue | `redis://redis:6379/0` | Use cluster for HA |
| `CELERY_RESULT_BACKEND` | Task result storage | Same as broker | Can be separate Redis instance |
| `FAISS_INDEX_PATH` | FAISS index file location | Auto-generated | Must be on shared filesystem |

**⚠️ Security Note:** Change default database credentials before deploying to production.

---

## Docker Compose Deployment

### 1. Build Images

Build all service images from scratch:

```bash
docker compose build
```

**Expected Output:**
```
[+] Building 120.5s (16/16) FINISHED
 => [backend internal] load build definition from Dockerfile
 => [backend] transferring dockerfile: 1.23kB
 => ...
 => [backend] exporting to image
```

### 2. Start All Services

Start the entire stack in detached mode:

```bash
docker compose up -d
```

**Services Started:**
- `db` — PostgreSQL database (port 5432)
- `redis` — Redis message broker (port 6379)
- `backend` — FastAPI application (port 8000)
- `worker` — Celery background worker
- `adminer` — Database admin UI (port 8080)

**Verify Services:**
```bash
docker compose ps
```

**Expected Output:**
```
NAME                COMMAND                  STATUS              PORTS
backend-backend-1   "uvicorn app.main:..."   Up 10 seconds       0.0.0.0:8000->8000/tcp
backend-db-1        "docker-entrypoint..."   Up 15 seconds       0.0.0.0:5432->5432/tcp
backend-redis-1     "docker-entrypoint..."   Up 12 seconds       0.0.0.0:6379->6379/tcp
backend-worker-1    "celery -A app.cor..."   Up 8 seconds
backend-adminer-1   "entrypoint.sh ph..."    Up 10 seconds       0.0.0.0:8080->8080/tcp
```

### 3. View Logs

**All services:**
```bash
docker compose logs -f
```

**Specific service:**
```bash
docker compose logs -f backend
docker compose logs -f worker
```

**Last 100 lines:**
```bash
docker compose logs --tail=100 backend
```

---

## Testing the Deployment

### 1. Health Check

```bash
curl http://localhost:8000/health
```

**Expected Response:**
```json
{"status": "healthy"}
```

### 2. Access Swagger Documentation

Open in your browser:
```
http://localhost:8000/docs
```

You should see the interactive API documentation.

### 3. Upload a Test Video

**Prepare a sample video:**
```bash
# Download a short sample video (or use your own)
wget https://sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4 -O sample.mp4
```

**Upload via curl:**
```bash
curl -X POST "http://localhost:8000/videos/upload" \
  -F "file=@sample.mp4" \
  -F "title=Test Video"
```

**Expected Response:**
```json
{
  "task_id": "d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f90",
  "status": "processing",
  "video_id": 1
}
```

### 4. Check Task Status

```bash
# Use the task_id from previous response
curl "http://localhost:8000/videos/tasks/d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f90"
```

**Monitor worker logs:**
```bash
docker compose logs -f worker
```

**Expected Log Output:**
```
worker-1  | [2025-09-14 10:30:15] Task process_video[d4e5f6a7...] received
worker-1  | [2025-09-14 10:30:18] Extracting audio from video...
worker-1  | [2025-09-14 10:30:45] Transcribing with Whisper...
worker-1  | [2025-09-14 10:31:20] Generating embeddings...
worker-1  | [2025-09-14 10:31:25] Task process_video[d4e5f6a7...] succeeded
```

### 5. Search Videos

```bash
curl "http://localhost:8000/videos/search?q=test&k=5"
```

**Expected Response:**
```json
[
  {
    "video_id": 1,
    "title": "Test Video",
    "path": "videos/abc123.mp4",
    "similarity_score": 0.85
  }
]
```

---

## Managing Services

### Stop All Services

```bash
docker compose down
```

### Stop Without Removing Containers

```bash
docker compose stop
```

### Restart a Single Service

```bash
docker compose restart backend
docker compose restart worker
```

### Rebuild After Code Changes

```bash
# Rebuild and restart
docker compose up --build -d

# Or rebuild specific service
docker compose build backend
docker compose up -d backend
```

### Scale Workers Horizontally

```bash
# Run 3 parallel Celery workers
docker compose up -d --scale worker=3
```

**Verify:**
```bash
docker compose ps
```

---

## Troubleshooting

### Issue 1: Database Connection Failed

**Symptom:**
```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Solution:**
```bash
# Check if database is running
docker compose ps db

# Restart database
docker compose restart db

# Check database logs
docker compose logs db
```

### Issue 2: Port Already in Use

**Symptom:**
```
Error starting userland proxy: listen tcp 0.0.0.0:8000: bind: address already in use
```

**Solution:**

**Option A: Stop conflicting process**
```bash
# Find process using port 8000
lsof -ti:8000

# Kill process (replace PID)
kill -9 <PID>
```

**Option B: Change port in docker-compose.yml**
```yaml
services:
  backend:
    ports:
      - "8001:8000"  # Change host port to 8001
```

### Issue 3: Worker Not Processing Tasks

**Symptom:**
- Task stays in `PENDING` status forever
- No logs in worker container

**Solution:**
```bash
# Check worker status
docker compose logs worker

# Restart worker
docker compose restart worker

# Verify Redis connection
docker compose exec redis redis-cli ping
# Expected: PONG
```

### Issue 4: FAISS Index Not Found

**Symptom:**
```
FileNotFoundError: [Errno 2] No such file or directory: '/app/media/faiss_index.faiss'
```

**Solution:**
```bash
# Create media directory
docker compose exec backend mkdir -p /app/media

# Re-process a video to rebuild index
# (Delete and re-upload)
```

### Issue 5: Out of Memory (Whisper Loading)

**Symptom:**
```
Killed
# Worker container crashes
```

**Solution:**

**Increase Docker memory limit:**

**Docker Desktop (macOS/Windows):**
- Settings → Resources → Memory → Increase to 8 GB

**Linux:**
```bash
# Check available memory
free -h

# If low, consider using Whisper tiny model
# Edit backend/app/services/video_processing.py:
# model = whisper.load_model("tiny")  # Instead of "base"
```

### Issue 6: Permission Denied on Media Directory

**Symptom:**
```
PermissionError: [Errno 13] Permission denied: '/app/media/videos/abc123.mp4'
```

**Solution:**
```bash
# Fix ownership (run on host)
sudo chown -R $USER:$USER backend/app/media

# Or inside container
docker compose exec backend chown -R appuser:appuser /app/media
```

---

## Production Considerations

### 1. Security Hardening

**Change Default Credentials:**
```bash
# Generate strong password
openssl rand -base64 32

# Update .env
DATABASE_URL=postgresql://produser:STRONG_PASSWORD_HERE@db:5432/proddb
```

**Disable Debug Mode:**
```bash
DEBUG=false
```

**Use HTTPS:**
- Place Nginx reverse proxy in front of FastAPI
- Obtain SSL certificate (Let's Encrypt)

**Network Isolation:**
```yaml
# docker-compose.yml
services:
  backend:
    networks:
      - frontend
  db:
    networks:
      - backend  # Not exposed to internet
```

### 2. Data Persistence

**Volume Backup Strategy:**
```bash
# Backup PostgreSQL
docker compose exec db pg_dump -U postgres userdb > backup.sql

# Backup media files
tar -czf media_backup.tar.gz backend/app/media

# Restore
docker compose exec -T db psql -U postgres userdb < backup.sql
```

### 3. Monitoring & Logging

**Integrate Logging:**
```yaml
services:
  backend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

**Add Monitoring:**
- Prometheus for metrics
- Grafana for dashboards
- Sentry for error tracking

### 4. Scaling for Production

**Load Balancer Configuration:**
```yaml
services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - backend
```

**Multiple Backend Instances:**
```bash
docker compose up -d --scale backend=3
```

**Database Replication:**
- Use PostgreSQL streaming replication
- Configure read replicas for search queries

### 5. Environment-Specific Configurations

**Development:**
```bash
docker compose -f docker-compose.yml up
```

**Production:**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**Example `docker-compose.prod.yml`:**
```yaml
services:
  backend:
    environment:
      DEBUG: "false"
    restart: always
  worker:
    restart: always
```

---

## Maintenance Tasks

### Rebuild FAISS Index

**When to rebuild:**
- After deleting videos
- Index corruption
- Upgrading embedding model

**Steps:**
```bash
# 1. Stop worker
docker compose stop worker

# 2. Delete existing index
docker compose exec backend rm -f /app/media/faiss_index.faiss /app/media/faiss_mapping.pkl

# 3. Re-process all videos
# (Manually or via script)

# 4. Restart worker
docker compose start worker
```

### Clear Media Files

**Remove all uploaded videos:**
```bash
docker compose exec backend rm -rf /app/media/videos/*
```

**Clear entire media directory:**
```bash
docker compose down
rm -rf backend/app/media/*
docker compose up -d
```

### Database Migrations

**Using Alembic (recommended for schema changes):**

```bash
# Install Alembic
pip install alembic

# Initialize
alembic init alembic

# Create migration
alembic revision --autogenerate -m "Add column X"

# Apply migration
docker compose exec backend alembic upgrade head
```

### Update Dependencies

**Update Python packages:**
```bash
# Edit backend/requirements.txt
# Then rebuild
docker compose build backend
docker compose up -d backend worker
```

---

## Running Tests

### Test Service Configuration

The project includes a dedicated test service that uses:
- SQLite in-memory database (fast, isolated)
- Celery memory broker (no Redis needed)
- Mocked heavy operations (Whisper, embeddings)

**Run full test suite:**
```bash
docker compose --profile test run --rm test
```

**Run specific test file:**
```bash
docker compose --profile test run --rm test pytest tests/test_users.py -v
```

**Run with coverage:**
```bash
docker compose --profile test run --rm test pytest --cov=app --cov-report=html
```

---

## Summary

This deployment guide provides:

✅ **Complete setup** — From prerequisites to production  
✅ **Troubleshooting** — Common issues and solutions  
✅ **Security best practices** — Hardening for production  
✅ **Maintenance procedures** — Backups, updates, rebuilds  

For API usage, see [api_reference.md](./api_reference.md).  
For architecture details, see [architecture.md](./architecture.md).
