"""Prometheus metrics and structured logging."""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from .config import settings

REGISTRY = CollectorRegistry(auto_describe=True)
BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.02, 0.035, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0)

requests_total = Counter(
    "streamrank_requests_total", "HTTP requests", ["route", "method", "status"], registry=REGISTRY
)
serve_latency = Histogram(
    "streamrank_serve_seconds", "Recommendation latency end to end", ["surface", "variant"], buckets=BUCKETS, registry=REGISTRY
)
stage_latency = Histogram(
    "streamrank_stage_seconds", "Per-stage latency", ["stage"], buckets=BUCKETS, registry=REGISTRY
)
candidates_gauge = Histogram(
    "streamrank_candidates", "Candidate set size per request", ["source"],
    buckets=(10, 25, 50, 100, 150, 200, 300, 500), registry=REGISTRY,
)
impressions_total = Counter(
    "streamrank_impressions_total", "Served recommendation sets", ["variant", "surface"], registry=REGISTRY
)
clicks_total = Counter("streamrank_clicks_total", "Clicks attributed to an impression", ["variant"], registry=REGISTRY)
events_total = Counter("streamrank_events_total", "Behavioural events ingested", ["event"], registry=REGISTRY)
feature_lag = Gauge("streamrank_feature_lag_seconds", "Age of the newest durable feature write", registry=REGISTRY)
sessions_gauge = Gauge("streamrank_active_sessions", "Sessions with a live feature vector", registry=REGISTRY)
index_size = Gauge("streamrank_index_items", "Items in the retrieval index", registry=REGISTRY)
cold_start_total = Counter("streamrank_cold_start_total", "Requests served without a user vector", registry=REGISTRY)
model_up = Gauge("streamrank_model_loaded", "1 when trained artifacts are loaded", registry=REGISTRY)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if settings.log_json else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel("WARNING")
    return logging.getLogger("streamrank")


def log_event(logger: logging.Logger, level: str, message: str, **fields: Any) -> None:
    logger.log(getattr(logging, level.upper(), logging.INFO), message, extra={"extra_fields": fields})
