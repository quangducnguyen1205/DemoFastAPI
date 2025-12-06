# Video Similarity Search System - Project Documentation

## Project Overview

This project implements a **content-based video similarity search system** using semantic analysis of video transcripts. The system allows users to upload videos, automatically transcribes their audio content, generates semantic embeddings, and enables similarity-based search queries.

### Problem Statement

In the era of massive video content, finding relevant videos based on semantic meaning rather than just metadata (title, tags) is a critical challenge. Traditional keyword-based search often fails to capture the contextual meaning of video content. This project addresses that gap by:

1. **Automatically transcribing** video audio using speech recognition
2. **Generating semantic embeddings** from transcripts using transformer models
3. **Indexing embeddings** in a high-performance vector database (FAISS)
4. **Enabling semantic search** where queries match content meaning, not just keywords

### Current Scope

**Project Focus: Content Similarity Only**

This implementation currently focuses exclusively on **content-based similarity** derived from video transcripts:

- ✅ Audio transcription using OpenAI Whisper
- ✅ Text segmentation and embedding generation (sentence-transformers)
- ✅ Vector similarity search using FAISS (CPU)
- ✅ REST API for upload, search, and task monitoring

**Out of Scope (Future Extensions):**
- ❌ Visual feature extraction (frame analysis, object detection)
- ❌ Audio fingerprinting (music/sound similarity)
- ❌ Metadata-based filtering (duration, resolution, upload date)
- ❌ Multi-modal fusion (combining text + visual + audio signals)

### System Architecture Overview

The system follows a **microservices-inspired architecture** with clear separation of concerns:

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│   Client    │─────▶│  FastAPI     │─────▶│ PostgreSQL  │
│  (API User) │      │   Backend    │      │   Database  │
└─────────────┘      └──────────────┘      └─────────────┘
                            │
                            │ enqueue task
                            ▼
                     ┌──────────────┐      ┌─────────────┐
                     │    Redis     │─────▶│   Celery    │
                     │   (Broker)   │      │   Worker    │
                     └──────────────┘      └─────────────┘
                                                  │
                                                  │ process
                                                  ▼
                                           ┌─────────────┐
                                           │   FAISS     │
                                           │   Index     │
                                           └─────────────┘
```

**Key Components:**

1. **FastAPI Backend** — REST API handling uploads, search requests, and status queries
2. **PostgreSQL Database** — Persistent storage for users, videos, and transcript segments
3. **Redis** — Message broker for asynchronous task queue
4. **Celery Worker** — Background processor for video transcription and embedding generation
5. **FAISS Index** — High-performance vector similarity search (CPU-based)

### Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **API Framework** | FastAPI | High-performance async REST API |
| **Database** | PostgreSQL 15 | Relational data persistence |
| **Task Queue** | Celery + Redis | Asynchronous background processing |
| **Transcription** | OpenAI Whisper | Speech-to-text conversion |
| **Embeddings** | sentence-transformers | Semantic text encoding (all-MiniLM-L6-v2) |
| **Vector Search** | FAISS (CPU) | Approximate nearest neighbor search |
| **Containerization** | Docker + Docker Compose | Service orchestration |
| **Testing** | pytest + httpx | Unit and integration testing |

### Quick Start (Docker Compose)

**Prerequisites:**
- Docker 20.10+ and Docker Compose v2+
- 4GB+ RAM recommended (Whisper model loading)
- Git (for cloning the repository)

**Steps:**

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd DemoFirstBackend
   ```

2. **Create environment configuration**
   ```bash
   cp .env.example .env
   # Edit .env if needed (defaults work for Docker Compose)
   ```

3. **Start all services**
   ```bash
   docker compose up --build
   ```

   This will start:
   - PostgreSQL (port 5432)
   - Redis (port 6379)
   - FastAPI backend (port 8000)
   - Celery worker (background)
   - Adminer database UI (port 8080)

4. **Access the API**
   - Swagger UI: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc
   - Health check: http://localhost:8000/health

5. **Upload a video**
   ```bash
   curl -X POST "http://localhost:8000/videos/upload" \
     -F "file=@sample.mp4" \
     -F "title=Sample Video"
   ```

   Response:
   ```json
   {
     "task_id": "abc-123-def",
     "status": "processing",
     "video_id": 1
   }
   ```

6. **Check task status**
   ```bash
   curl "http://localhost:8000/videos/tasks/abc-123-def"
   ```

7. **Search videos**
   ```bash
   curl "http://localhost:8000/videos/search?q=machine%20learning&k=5"
   ```

### Running Tests

The project includes a dedicated test service using SQLite and in-memory Celery:

```bash
# Run full test suite
docker compose run --rm test

# Run specific test file
docker compose run --rm test pytest tests/test_users.py -v

# Run with coverage
docker compose run --rm test pytest --cov=app --cov-report=term
```

### Stopping Services

```bash
# Stop all services
docker compose down

# Remove volumes (clears database)
docker compose down -v
```

---

## Directory Structure

```
DemoFirstBackend/
├── backend/
│   ├── app/
│   │   ├── config/          # Environment settings
│   │   ├── core/            # Database + Celery setup
│   │   ├── models/          # SQLAlchemy ORM models
│   │   ├── routers/         # API endpoints
│   │   ├── schemas/         # Pydantic request/response models
│   │   ├── services/        # Business logic (video processing, embeddings)
│   │   ├── tasks/           # Celery background tasks
│   │   └── main.py          # FastAPI application entry point
│   ├── tests/               # Pytest test suite
│   ├── Dockerfile           # Container definition
│   └── requirements.txt     # Python dependencies
├── docs/                    # Project documentation (this folder)
├── docker-compose.yml       # Service orchestration
├── .env.example             # Environment template
└── README.md                # Quick reference guide
```

---

## Academic Context

This project serves as **Project 1** for demonstrating:

1. **System Design** — Microservices architecture with clear separation of concerns
2. **Data Engineering** — ETL pipeline (extract audio → transcribe → embed → index)
3. **Machine Learning Integration** — Practical application of NLP models (Whisper, sentence-transformers)
4. **Software Engineering** — RESTful API design, containerization, testing, documentation
5. **Scalability Considerations** — Asynchronous processing, vector indexing, database design

For detailed technical documentation, see:
- [architecture.md](./architecture.md) — System design and data flow
- [api_reference.md](./api_reference.md) — Complete API documentation
- [deployment_guide.md](./deployment_guide.md) — Advanced deployment scenarios
- [future_work.md](./future_work.md) — Roadmap and extensions

---

## License

This project is for academic purposes. All dependencies respect their respective licenses.

## Contact

For questions or collaboration, please contact the project maintainer.
