# syntax=docker/dockerfile:1
# HyperTFT API — container image for Google Cloud Run.
FROM python:3.11-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so this layer caches unless requirements change.
# psycopg[binary] ships manylinux wheels, so no compiler/system libs needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the application code (.dockerignore keeps .env, .venv, caches out).
COPY . .

# Cloud Run sends traffic to $PORT (default 8080) and expects the app to
# listen on 0.0.0.0. Shell form so ${PORT} expands at runtime.
EXPOSE 8080
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
