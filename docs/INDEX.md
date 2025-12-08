# Documentation Index

Use this file as the single entry point into the documentation set. Each section links to a Markdown document inside `docs/`.

| Doc | Summary |
|-----|---------|
| [README.md](./README.md) | Project overview, Docker-based quickstart, upload/search walkthrough, owner filtering tips, and integration test instructions. |
| [api_reference.md](./api_reference.md) | Canonical list of supported endpoints (root, health, users, video upload/task/list/detail/delete, semantic search with optional `owner_id`). Includes request/response samples. |
| [architecture.md](./architecture.md) | Design notes covering the async façade, Celery workflow, FAISS integration, and how owner-aware filtering is enforced. |
| [deployment_guide.md](./deployment_guide.md) | Step-by-step Compose deployment, environment configuration, operational commands, troubleshooting, and the new one-file integration test process. |
| [future_work.md](./future_work.md) | Roadmap items such as authentication/authorization, admin-only routing, and richer semantic search capabilities. |

All other files (screenshots, diagrams, etc.) should reference this index when new sections are added.
