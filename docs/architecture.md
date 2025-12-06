# System Architecture

## Table of Contents
1. [High-Level Architecture](#high-level-architecture)
2. [Core Modules](#core-modules)
3. [Data Flow](#data-flow)
4. [Component Interactions](#component-interactions)
5. [FAISS Integration](#faiss-integration)
6. [Database Schema](#database-schema)

---

## High-Level Architecture

The system follows a **layered microservices architecture** with asynchronous task processing:

```
┌──────────────────────────────────────────────────────────────┐
│                        Client Layer                           │
│  (HTTP Clients, Browsers, Mobile Apps, Postman, curl)        │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP/REST
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                     API Layer (FastAPI)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Users      │  │   Videos     │  │   Tasks      │      │
│  │   Router     │  │   Router     │  │   Router     │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└───────────────────────────┬──────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  PostgreSQL  │   │    Redis     │   │   Celery     │
│   Database   │   │   (Broker)   │   │   Worker     │
└──────────────┘   └──────────────┘   └──────┬───────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │  FAISS Index     │
                                    │  (Vector Store)  │
                                    └──────────────────┘
```

### Architecture Principles

1. **Separation of Concerns** — Each layer has a clear, single responsibility
2. **Asynchronous Processing** — Long-running tasks (transcription, embedding) don't block API responses
3. **Loose Coupling** — Components communicate via well-defined interfaces (REST, message queue)
4. **Stateless API** — FastAPI backend can scale horizontally
5. **Persistent State** — PostgreSQL for structured data, FAISS for vector index

---

## Core Modules

### 1. `app/main.py` — Application Entry Point

**Purpose:** FastAPI application initialization and lifecycle management.

**Key Responsibilities:**
- Load environment variables via `python-dotenv`
- Register API routers (users, videos)
- Create database tables on startup (lifespan handler)
- Provide health check and root endpoints

**Code Snippet:**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()  # Ensure schema exists
    yield

app = FastAPI(
    title="Video Similarity Search API",
    lifespan=lifespan
)
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(videos.router, prefix="/videos", tags=["videos"])
```

### 2. `app/config/settings.py` — Configuration Management

**Purpose:** Centralized environment-driven configuration.

**Key Settings:**
- `DATABASE_URL` — PostgreSQL connection string (or SQLite for tests)
- `MEDIA_ROOT` — Base path for uploaded videos and FAISS files
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` — Redis endpoints
- `FAISS_INDEX_PATH` / `FAISS_MAPPING_PATH` — Vector index persistence

**Design Pattern:** Singleton settings object with environment fallbacks.

### 3. `app/core/database.py` — Database Engine

**Purpose:** SQLAlchemy engine and session factory.

**Key Components:**
```python
engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()  # ORM base class

def get_db():  # Dependency injection for FastAPI routes
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 4. `app/core/celery_app.py` — Task Queue

**Purpose:** Celery application for background processing.

**Configuration:**
- Broker: Redis (message queue)
- Backend: Redis (result storage)
- Task discovery: Auto-imports from `app.tasks.video_tasks`

**Why Celery?**
- Video transcription can take 30s–2min per video
- Embedding generation requires heavy ML models (sentence-transformers)
- Prevents API timeouts and enables horizontal scaling

### 5. `app/models/` — ORM Models

**Files:**
- `user.py` — User account (id, username, email, timestamps)
- `video.py` — Video metadata (id, title, path, status, owner_id)
- `transcript.py` — Transcript segments (video_id, segment_index, text, embeddings)

**Relationships:**
- User → Videos (one-to-many)
- Video → Transcripts (one-to-many, cascade delete)

### 6. `app/routers/` — API Endpoints

**Structure:**
- `users.py` — User CRUD operations
- `videos.py` — Video upload, search, CRUD, task status

**Key Design:**
- Pydantic schemas for request validation and response serialization
- Dependency injection for database sessions
- HTTPException for error handling

### 7. `app/services/` — Business Logic

**Purpose:** Encapsulate complex processing logic outside routers.

**Modules:**

#### `video_processing.py`
- `extract_audio_to_wav()` — Uses ffmpeg to extract mono WAV
- `transcribe_audio_with_whisper()` — Loads Whisper model and transcribes
- `segment_text()` — Splits transcript into ~200-char chunks
- `persist_transcript_segments()` — Saves segments to database
- `embed_and_update_faiss()` — Generates embeddings and updates index

#### `semantic_index/`
- `__init__.py` — Embedding generation using sentence-transformers
- `reader.py` — Read-only FAISS index operations (load, search)
- `writer.py` — Write operations (create index, add vectors, save)

**Design Rationale:**
- Separation of read/write prevents race conditions
- Lazy model loading (singleton pattern) reduces memory overhead
- Thread-safe initialization using `threading.Lock()`

### 8. `app/tasks/video_tasks.py` — Celery Tasks

**Main Task:** `process_video_task(video_id, abs_video_path)`

**Workflow:**
1. Query video record from database
2. Extract audio → Transcribe → Segment text
3. Persist transcript segments to database
4. Generate embeddings for each segment
5. Add embeddings to FAISS index
6. Update video status to "ready" or "failed"

**Error Handling:**
- Try-except wraps entire pipeline
- Failures update video status to "failed" with error message
- Database session always closed in `finally` block

---

## Data Flow

### Video Upload Flow

```
1. Client uploads video file
   ↓
2. FastAPI endpoint receives file
   ↓
3. Save file to disk (unique filename)
   ↓
4. Create Video record (status="processing")
   ↓
5. Enqueue Celery task with video_id + path
   ↓
6. Return task_id + video_id to client
   ↓ (API response complete)
7. Celery worker picks up task
   ↓
8. Extract audio (ffmpeg)
   ↓
9. Transcribe audio (Whisper)
   ↓
10. Segment transcript text
    ↓
11. Save segments to database
    ↓
12. Generate embeddings (sentence-transformers)
    ↓
13. Add to FAISS index
    ↓
14. Update video status to "ready"
```

### Search Flow

```
1. Client sends search query "machine learning"
   ↓
2. FastAPI endpoint receives query string
   ↓
3. Generate query embedding (sentence-transformers)
   ↓
4. Load FAISS index + segment→video mapping
   ↓
5. Search index for top-k nearest vectors
   ↓
6. Map segment IDs to video IDs
   ↓
7. Group by video, keep best similarity per video
   ↓
8. Query database for video metadata
   ↓
9. Return sorted list of videos with scores
```

---

## Component Interactions

### API ↔ Database

**Pattern:** Dependency injection via `get_db()`

```python
@router.get("/videos/")
def list_videos(db: Session = Depends(get_db)):
    return db.query(models.Video).all()
```

- Connection pooling managed by SQLAlchemy
- Session lifecycle: created per request, closed after response

### API ↔ Celery

**Pattern:** Fire-and-forget task enqueue

```python
async_result = process_video_task.delay(video_id, path)
return {"task_id": async_result.id}
```

- API doesn't wait for task completion
- Client polls `/videos/tasks/{task_id}` for status
- Task results stored in Redis backend

### Celery Worker ↔ Database

**Pattern:** Direct session creation (not via FastAPI dependency injection)

```python
db = SessionLocal()
try:
    # ... process video ...
    db.commit()
finally:
    db.close()
```

- Worker creates independent database connections
- No shared state with API server

### Celery Worker ↔ FAISS

**Pattern:** File-based index persistence

```python
# Writer adds embeddings
index = faiss.IndexFlatL2(dim)
index.add(vectors)
faiss.write_index(index, FAISS_INDEX_PATH)

# Reader loads index
index = faiss.read_index(FAISS_INDEX_PATH)
distances, indices = index.search(query_vec, k)
```

- Index stored on shared filesystem (Docker volume)
- Both API and worker can read index
- Only worker writes to index (no concurrent write issues)

---

## FAISS Integration

### What is FAISS?

**FAISS** (Facebook AI Similarity Search) is a library for efficient similarity search and clustering of dense vectors.

**Why FAISS?**
- Handles millions of vectors with sub-second search latency
- CPU and GPU implementations available
- Supports approximate nearest neighbor (ANN) algorithms
- Open-source and battle-tested (Meta AI)

### Index Type

**Current:** `IndexFlatL2` (brute-force L2 distance)

**Characteristics:**
- Exact search (no approximation)
- Simple to implement and debug
- Works well for <100k vectors
- O(n) search complexity

**Production Alternative:** `IndexIVFFlat` or `IndexHNSW` for faster search on large datasets.

### Vector Dimensionality

**Model:** `all-MiniLM-L6-v2` (sentence-transformers)
- Embedding dimension: **384**
- Fast inference (~1ms per sentence on CPU)
- Good balance of speed and quality

### Mapping Structure

**Problem:** FAISS index stores vectors by position (0, 1, 2, ...), but we need to map back to video IDs.

**Solution:** Maintain a separate pickle file `faiss_mapping.pkl`:

```python
{
    0: video_id_1,  # FAISS index 0 → video 1
    1: video_id_1,  # FAISS index 1 → video 1 (another segment)
    2: video_id_3,  # FAISS index 2 → video 3
    ...
}
```

**Update Process:**
- When adding N embeddings for video V, extend mapping with `[V] * N`
- Persist mapping atomically after updating index

### Search Algorithm

**Pseudocode:**
```python
1. query_embedding = encode(query_text)
2. distances, indices = faiss_index.search(query_embedding, k=50)
3. best_per_video = {}
4. for idx, dist in zip(indices, distances):
       video_id = mapping[idx]
       similarity = 1 / (1 + dist)  # Convert L2 to bounded score
       best_per_video[video_id] = max(best_per_video[video_id], similarity)
5. Sort videos by similarity descending
6. Return top k videos
```

**Key Insight:** Multiple transcript segments from the same video → deduplicate by keeping highest similarity score.

---

## Database Schema

### Table: `users`

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| username | VARCHAR(100) | NOT NULL, UNIQUE |
| email | VARCHAR(255) | NOT NULL, UNIQUE |
| created_at | TIMESTAMP | DEFAULT NOW() |

### Table: `videos`

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| title | VARCHAR(255) | NOT NULL |
| description | TEXT | NULL |
| url | VARCHAR(500) | NOT NULL |
| path | VARCHAR(500) | NULL, INDEXED |
| owner_id | INTEGER | FK → users.id, NULL |
| status | VARCHAR(50) | NULL ('processing', 'ready', 'failed') |
| created_at | TIMESTAMP | DEFAULT NOW() |
| updated_at | TIMESTAMP | ON UPDATE NOW() |

### Table: `transcripts`

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| video_id | INTEGER | FK → videos.id, NOT NULL |
| segment_index | INTEGER | NOT NULL |
| text | TEXT | NOT NULL |
| created_at | TIMESTAMP | DEFAULT NOW() |

**Indexes:**
- `videos.path` — Fast lookup by file path
- `videos.owner_id` — Query videos by user
- `transcripts.video_id` — Join transcripts with videos

**Cascade Behavior:**
- Deleting a video → automatically deletes all associated transcripts

---

## Scalability Considerations

### Current Limitations
- Single FAISS index file (no sharding)
- Single Celery worker instance
- CPU-only embeddings and search

### Scaling Strategies

**Horizontal Scaling:**
1. Deploy multiple Celery workers (Kubernetes, Docker Swarm)
2. Use distributed Redis cluster
3. Add load balancer in front of multiple FastAPI instances

**FAISS Optimization:**
1. Switch to `IndexIVFFlat` with 100–1000 clusters for >1M vectors
2. Enable GPU acceleration (`faiss-gpu` library)
3. Consider quantization (`IndexIVFPQ`) to reduce memory footprint

**Database Optimization:**
1. Add read replicas for search queries
2. Partition `transcripts` table by video_id (PostgreSQL partitioning)
3. Cache frequent searches in Redis

**Storage:**
1. Move media files to S3/GCS for durability
2. Use CDN for video delivery
3. Separate FAISS index to dedicated storage server

---

## Summary

This architecture provides a **solid foundation** for a video similarity search system with:

✅ **Clear separation** of API, processing, and storage layers  
✅ **Asynchronous processing** for scalability  
✅ **Efficient vector search** using FAISS  
✅ **Extensible design** for future features  

For deployment instructions, see [deployment_guide.md](./deployment_guide.md).  
For API details, see [api_reference.md](./api_reference.md).
