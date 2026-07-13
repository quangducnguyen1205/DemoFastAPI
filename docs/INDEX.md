# Documentation Index

This branch documents Repo A as an internal processing dependency for the integrated product.

| Doc | Summary |
|-----|---------|
| [README.md](./README.md) | Branch overview, service responsibility, Kafka intake/result relay, and quickstart for the processing-only runtime. |
| [api_reference.md](./api_reference.md) | Current HTTP contract for transitional upload, task polling, video status lookup, and transcript retrieval. |
| [architecture.md](./architecture.md) | Processing-service architecture, Kafka consumer/result relay, idempotency, persistence boundaries, and worker flow. |
| [architecture/processing_pipeline.md](./architecture/processing_pipeline.md) | P3-S5.B3 responsibility map, feature ports/adapters, durable result delivery, and explicit runtime composition. |
| [deployment_guide.md](./deployment_guide.md) | Practical Docker Compose runbook for backend, worker, consumer, db, redis, and runtime validation. |
| [transcript_chunking.md](./transcript_chunking.md) | Current transcript chunking behavior and why it is shaped for downstream transcript retrieval. |
