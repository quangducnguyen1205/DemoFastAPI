# Documentation Index

This repository documents FastAPI as the internal processing/provider dependency for the
Project3 integrated product.

Cross-repository v1 ownership and validation are summarized in the
[Project3 final baseline](https://github.com/quangducnguyen1205/ai-knowledge-workspace/blob/project3-submission-v1/docs/submission/project3-final-baseline.md).

| Doc | Summary |
|-----|---------|
| [README.md](./README.md) | Branch overview, service responsibility, Kafka intake/result relay, and quickstart for the processing-only runtime. |
| [api_reference.md](./api_reference.md) | Current HTTP contract for transitional upload, task polling, video status lookup, and transcript retrieval. |
| [architecture.md](./architecture.md) | Processing-service architecture, Kafka consumer/result relay, idempotency, persistence boundaries, and worker flow. |
| [architecture/processing_pipeline.md](./architecture/processing_pipeline.md) | P3-S5.B3 responsibility map, feature ports/adapters, durable result delivery, and explicit runtime composition. |
| [deployment_guide.md](./deployment_guide.md) | Practical Docker Compose runbook for backend, worker, consumer, db, redis, and runtime validation. |
| [transcript_chunking.md](./transcript_chunking.md) | Current transcript chunking behavior and why it is shaped for downstream transcript retrieval. |
