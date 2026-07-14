# Project3 FastAPI processing agent entrypoint

Read the central [Project3 engineering skill](https://github.com/quangducnguyen1205/ai-knowledge-workspace/blob/main/ai-guidance/skills/project3-engineering/SKILL.md)
and the [v1 final baseline](https://github.com/quangducnguyen1205/ai-knowledge-workspace/blob/project3-submission-v1/docs/submission/project3-final-baseline.md)
before editing. This repository is an internal processing/provider service; it does not own
public product state, workspace authorization or browser-facing APIs.

Before Python processing, result delivery, provider or Compose work, read the central
[change-safety](https://github.com/quangducnguyen1205/ai-knowledge-workspace/blob/main/ai-guidance/skills/project3-engineering/checklists/change-safety.md),
[fastapi-processing](https://github.com/quangducnguyen1205/ai-knowledge-workspace/blob/main/ai-guidance/skills/project3-engineering/checklists/fastapi-processing.md)
and, for runtime work, [runtime-validation](https://github.com/quangducnguyen1205/ai-knowledge-workspace/blob/main/ai-guidance/skills/project3-engineering/checklists/runtime-validation.md).

Canonical checks are:

```text
PYTHONPATH=backend python -m unittest discover -s backend -p 'test_*.py'
python -m compileall -q backend/app
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.project3.yml config --quiet
```

Keep request consumption, Celery handoff, artifacts, result outbox/relay and provider
boundaries stable. Preserve direct-processing compatibility. Do not add import-time I/O,
real Kafka/DB/Celery/MinIO/Ollama calls to unit tests, or change event/API contracts.

Inspect `git status` first, work on `main`, commit locally after validation, never stage
`.local-notes`, and never push or start runtime services without explicit authorization.
