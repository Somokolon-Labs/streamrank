"""StreamRank API - real-time two-stage recommendations."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from .config import settings
from .db import dispose, init_db, ping
from .observability import log_event, render_metrics, requests_total, setup_logging
from .schemas import (
    EventRequest,
    EventResponse,
    HealthResponse,
    RecommendRequest,
    RecommendResponse,
    SimulateRequest,
)
from .service import background_loop, service

log = setup_logging()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
STARTED_AT = time.time()
_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await service.start()
    task = asyncio.create_task(background_loop(log))
    _tasks.add(task)
    log_event(
        log,
        "info",
        "streamrank ready",
        items=len(service.catalog),
        model_loaded=service.artifacts.loaded,
        factors=service.artifacts.factors,
        ivf_clusters=len(service.artifacts.centroids) if service.artifacts.centroids is not None else 0,
    )
    try:
        yield
    finally:
        for pending in _tasks:
            pending.cancel()
        await service.flush()
        await dispose()


app = FastAPI(
    title="StreamRank",
    version=settings.version,
    description=(
        "Real-time recommendation service. Two-stage retrieval (ALS embeddings over an IVF index, "
        "co-visitation and trending) feeding a gradient-boosted ranker, with streaming session "
        "features that change the next response within milliseconds."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Response-Time-Ms"],
)


@app.middleware("http")
async def timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
    requests_total.labels(route=request.scope.get("path", "?"), method=request.method, status=str(response.status_code)).inc()
    return response


async def require_key(key: str | None = Depends(api_key_header)) -> str:
    if not settings.require_api_key:
        return key or "anonymous"
    if key and key in settings.api_key_set:
        return key
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid X-API-Key")


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
@app.get("/", tags=["meta"])
async def root() -> dict[str, Any]:
    return {
        "name": "StreamRank",
        "version": settings.version,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "items": len(service.catalog),
        "model_loaded": service.artifacts.loaded,
        "docs": "/docs",
        "endpoints": ["/v1/recommend", "/v1/events", "/v1/stats", "/v1/experiment", "/v1/catalog", "/metrics"],
    }


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=settings.version, checks={"process": "alive"})


@app.get("/health/ready", response_model=HealthResponse, tags=["meta"])
async def ready(response: Response) -> HealthResponse:
    db_ok = await ping()
    ok = db_ok and bool(service.catalog)
    response.status_code = 200 if ok else 503
    return HealthResponse(
        status="ready" if ok else "degraded",
        version=settings.version,
        checks={
            "database": db_ok,
            "catalog_items": len(service.catalog),
            "model_loaded": service.artifacts.loaded,
            "ranker": service.artifacts.ranker is not None,
        },
    )


@app.get("/metrics", tags=["meta"])
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


# ---------------------------------------------------------------------------
# serving
# ---------------------------------------------------------------------------
@app.post("/v1/recommend", response_model=RecommendResponse, tags=["serving"])
async def recommend(body: RecommendRequest, key: str = Depends(require_key)) -> Any:
    if not service.catalog:
        raise HTTPException(status_code=503, detail="catalog is empty - run ml/generate_data.py")
    return await service.recommend(body)


@app.post("/v1/events", response_model=EventResponse, status_code=202, tags=["serving"])
async def ingest_event(body: EventRequest, key: str = Depends(require_key)) -> Any:
    if body.item_id not in service.catalog:
        raise HTTPException(status_code=422, detail=f"unknown item '{body.item_id}'")
    return await service.ingest(body)


@app.post("/v1/events/batch", status_code=202, tags=["serving"])
async def ingest_batch(body: list[EventRequest], key: str = Depends(require_key)) -> dict[str, Any]:
    accepted = 0
    for event in body[:500]:
        if event.item_id in service.catalog:
            await service.ingest(event)
            accepted += 1
    return {"accepted": accepted, "rejected": len(body) - accepted}


# ---------------------------------------------------------------------------
# catalog + analytics
# ---------------------------------------------------------------------------
@app.get("/v1/catalog", tags=["catalog"])
async def catalog(
    category: str | None = None,
    limit: int = Query(default=48, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = list(service.catalog.values())
    if category:
        rows = [row for row in rows if row["category"] == category]
    return {
        "count": len(rows),
        "categories": sorted(service.categories.keys()),
        "items": rows[offset : offset + limit],
    }


@app.get("/v1/catalog/{item_id}", tags=["catalog"])
async def item_detail(item_id: str) -> dict[str, Any]:
    item = service.catalog.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@app.get("/v1/trending", tags=["catalog"])
async def trending(limit: int = Query(default=12, ge=1, le=48)) -> dict[str, Any]:
    return {"items": service.trending_items(limit)}


@app.get("/v1/stats", tags=["analytics"])
async def stats() -> dict[str, Any]:
    return service.stats()


@app.get("/v1/experiment", tags=["analytics"])
async def experiment() -> dict[str, Any]:
    return service.experiment_report()


@app.post("/v1/simulate", tags=["analytics"])
async def simulate(body: SimulateRequest, key: str = Depends(require_key)) -> dict[str, Any]:
    """Drive synthetic shoppers through the real serving path (demo + load)."""
    return await service.simulate(body.users, body.steps, body.surface, body.click_noise)


@app.post("/v1/reload", tags=["analytics"])
async def reload_artifacts(key: str = Depends(require_key)) -> dict[str, Any]:
    await service.start()
    return {
        "model_loaded": service.artifacts.loaded,
        "items": len(service.catalog),
        "trained_at": (service.artifacts.metrics or {}).get("trained_at"),
    }


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})
