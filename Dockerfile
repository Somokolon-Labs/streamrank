# Data generation + training happen at build time, so the image is self-contained
# and every deploy ships a model whose metrics are known.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY ml ./ml
COPY scripts ./scripts
COPY data/catalog_images.json ./data/catalog_images.json

# Deterministic dataset + model (~30s). Override with a mounted volume to bring
# your own catalogue and interaction log.
RUN python ml/generate_data.py --users 1500 --sessions 3 --seed 23 \
 && python ml/train.py --factors 32 --iterations 18 \
 && rm -f data/interactions.csv \
 && find /app -name "__pycache__" -type d -prune -exec rm -rf {} +

RUN useradd --create-home --uid 10001 stream \
 && mkdir -p /app/data \
 && chown -R stream:stream /app
USER stream

ENV DATABASE_URL=sqlite+aiosqlite:////app/data/streamrank.db \
    ARTIFACTS_DIR=/app/ml/artifacts \
    CATALOG_PATH=/app/data/catalog.json \
    PORT=8200

EXPOSE 8200

HEALTHCHECK --interval=20s --timeout=4s --start-period=25s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8200}/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8200} --proxy-headers --forwarded-allow-ips=* --workers ${WEB_CONCURRENCY:-1}"]
