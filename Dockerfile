# Root-level Dockerfile for VS Code docker-build task
# Mirrors backend/Dockerfile but with adjusted COPY paths
FROM python:3.12-slim

EXPOSE 8000

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libffi-dev \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pip requirements from backend
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip
RUN python -m pip install -r requirements.txt

# Copy application code from backend
COPY backend/. .

RUN adduser -u 5678 --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
