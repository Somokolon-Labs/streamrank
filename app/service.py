"""Serving orchestration: variants, impressions, experiment stats, persistence.

The request path never waits on the database. Impressions, interactions and
profile updates are buffered in memory and flushed in batches by a background
task, so p95 serving latency is bounded by CPU, not by storage.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .config import settings
from .db import iso, session_scope
from .models import CoVisit, Impression, Interaction, Item, ItemStat, UserProfile, utcnow
from .observability import (
    clicks_total,
    cold_start_total,
    events_total,
    feature_lag,
    impressions_total,
    log_event,
    serve_latency,
)
from .recsys import Artifacts, FeatureStore, Ranker, Retriever, diversify, load_artifacts

REASONS = {
    "ann": "similar to what you liked",
    "covisit": "often viewed together",
    "trending": "trending now",
}


def bucket(user_id: str, experiment: str) -> float:
    digest = hashlib.sha256(f"{experiment}:{user_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


class RecommendationService:
    def __init__(self) -> None:
        self.artifacts: Artifacts = Artifacts()
        self.store: FeatureStore | None = None
        self.retriever: Retriever | None = None
        self.ranker: Ranker | None = None
        self.catalog: dict[str, dict] = {}
        self.categories: dict[str, list[str]] = defaultdict(list)
        self.latency_samples: deque[float] = deque(maxlen=2000)
        self.retrieval_samples: deque[float] = deque(maxlen=2000)
        self.ranking_samples: deque[float] = deque(maxlen=2000)
        self.request_times: deque[float] = deque(maxlen=5000)
        self.experiment: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.pending_interactions: list[dict] = []
        self.pending_impressions: list[dict] = []
        self.impression_index: dict[str, dict] = {}
        self.impression_order: deque[str] = deque(maxlen=8000)
        self.served_total = 0
        self.events_seen = 0
        self.started_at = time.time()
        self._lock = asyncio.Lock()

    # ---- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        self.artifacts = load_artifacts()
        self.store = FeatureStore(self.artifacts)
        await self.load_catalog()
        self.retriever = Retriever(self.artifacts, self.store)
        self.ranker = Ranker(self.artifacts, self.store, self.catalog)
        self.warmup()

    def warmup(self) -> None:
        """First predict_proba on a fresh tree ensemble costs ~150ms; pay it here."""
        if self.ranker is None or not self.catalog:
            return
        sample = list(self.catalog.keys())[:32]
        query = np.zeros(self.artifacts.factors, dtype=np.float32)
        for _ in range(2):
            try:
                self.ranker.score("warmup", sample, query, sample[:3], cold=True)
            except Exception:
                break

    async def load_catalog(self) -> None:
        path = Path(settings.catalog_path)
        rows: list[dict] = []
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8"))
            async with session_scope() as session:
                existing = int(await session.scalar(select(func.count()).select_from(Item)) or 0)
                if existing != len(rows):
                    for row in rows:
                        stmt = sqlite_insert(Item) if settings.is_sqlite else None
                        values = {
                            "id": row["id"],
                            "title": row["title"],
                            "brand": row["brand"],
                            "category": row["category"],
                            "price": row["price"],
                            "rating": row["rating"],
                            "tags": row.get("tags", []),
                            "image_url": row.get("image_url", ""),
                            "image_credit": row.get("image_credit", ""),
                            "alt_text": row.get("alt_text", ""),
                        }
                        if stmt is not None:
                            await session.execute(stmt.values(**values).on_conflict_do_update(index_elements=["id"], set_=values))
                        else:
                            merged = await session.get(Item, row["id"])
                            if merged is None:
                                session.add(Item(**values))
                            else:
                                for key, value in values.items():
                                    setattr(merged, key, value)
        else:
            async with session_scope() as session:
                items = (await session.execute(select(Item))).scalars().all()
            rows = [
                {
                    "id": item.id,
                    "title": item.title,
                    "brand": item.brand,
                    "category": item.category,
                    "price": item.price,
                    "rating": item.rating,
                    "tags": item.tags,
                    "image_url": item.image_url,
                    "image_credit": item.image_credit,
                    "alt_text": item.alt_text,
                }
                for item in items
            ]

        self.catalog = {row["id"]: row for row in rows}
        self.categories = defaultdict(list)
        for row in rows:
            self.categories[row["category"]].append(row["id"])

    # ---- experiment -----------------------------------------------------
    def variant_for(self, user_id: str, forced: str | None = None) -> str:
        if forced in (settings.control_variant, settings.treatment_variant):
            return forced
        return settings.control_variant if bucket(user_id, settings.experiment_name) < settings.control_share else settings.treatment_variant

    # ---- serve ----------------------------------------------------------
    async def recommend(self, request) -> dict[str, Any]:
        assert self.store and self.retriever and self.ranker
        started = time.perf_counter()
        session_id = request.session_id or request.user_id
        variant = self.variant_for(request.user_id, request.variant)
        query, cold = self.store.query_vector(request.user_id, session_id)
        session_items = self.store.session_items(session_id)
        exclude = set(request.exclude) | set(session_items[-3:])

        retrieval_started = time.perf_counter()
        if variant == settings.control_variant:
            candidate_ids = self.store.trending(settings.candidates_ann)
            stage_counts = {"trending": len(candidate_ids), "merged": len(candidate_ids)}
        else:
            candidate_ids, stage_counts = self.retriever.candidates(query, session_items)
        if request.category:
            allowed = set(self.categories.get(request.category, []))
            candidate_ids = [item_id for item_id in candidate_ids if item_id in allowed]
        if not candidate_ids:
            candidate_ids = list(self.catalog.keys())[: settings.candidates_ann]
            stage_counts["fallback"] = len(candidate_ids)
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000

        ranking_started = time.perf_counter()
        if variant == settings.control_variant:
            scores = np.array(
                [1.0 - index / max(1, len(candidate_ids)) for index in range(len(candidate_ids))], dtype=np.float32
            )
        else:
            scores = self.ranker.score(request.user_id, candidate_ids, query, session_items, cold)
        ranking_ms = (time.perf_counter() - ranking_started) * 1000

        if request.diversify and variant != settings.control_variant:
            picked = diversify(candidate_ids, scores, self.artifacts, self.catalog, request.limit, exclude=exclude)
        else:
            order = np.argsort(-scores)
            picked = [
                (candidate_ids[i], float(scores[i])) for i in order if candidate_ids[i] not in exclude
            ][: request.limit]

        ann_set = set(candidate_ids[: stage_counts.get("ann", 0)])
        covisit_set = set(self.retriever.covisit(session_items, settings.candidates_covisit)) if variant != settings.control_variant else set()
        feature_matrix = (
            self.ranker.features(request.user_id, [item_id for item_id, _ in picked], query, session_items, cold)
            if picked and variant != settings.control_variant
            else None
        )

        items: list[dict[str, Any]] = []
        for rank, (item_id, score) in enumerate(picked, start=1):
            item = self.catalog[item_id]
            if variant == settings.control_variant:
                reason = REASONS["trending"]
            elif item_id in covisit_set:
                reason = REASONS["covisit"]
            elif item_id in ann_set:
                reason = REASONS["ann"]
            else:
                reason = REASONS["trending"]
            features: dict[str, float] = {}
            if feature_matrix is not None:
                names = self.artifacts.feature_names or [f"f{i}" for i in range(feature_matrix.shape[1])]
                features = {name: round(float(value), 5) for name, value in zip(names, feature_matrix[rank - 1], strict=False)}
            items.append(
                {
                    "rank": rank,
                    "item_id": item_id,
                    "title": item["title"],
                    "brand": item["brand"],
                    "category": item["category"],
                    "price": item["price"],
                    "rating": item["rating"],
                    "image_url": item["image_url"],
                    "image_credit": item["image_credit"],
                    "alt_text": item["alt_text"] or item["title"],
                    "score": round(float(score), 5),
                    "reason": reason,
                    "features": features,
                }
            )

        total_ms = (time.perf_counter() - started) * 1000
        request_id = f"req_{uuid.uuid4().hex[:16]}"

        self.latency_samples.append(total_ms)
        self.retrieval_samples.append(retrieval_ms)
        self.ranking_samples.append(ranking_ms)
        self.request_times.append(time.time())
        self.served_total += 1
        serve_latency.labels(surface=request.surface, variant=variant).observe(total_ms / 1000)
        impressions_total.labels(variant=variant, surface=request.surface).inc()
        if cold:
            cold_start_total.inc()

        record = {
            "request_id": request_id,
            "user_id": request.user_id,
            "session_id": session_id,
            "surface": request.surface,
            "variant": variant,
            "item_ids": [item["item_id"] for item in items],
            "scores": [item["score"] for item in items],
            "stage_counts": stage_counts,
            "retrieval_ms": round(retrieval_ms, 3),
            "ranking_ms": round(ranking_ms, 3),
            "total_ms": round(total_ms, 3),
            "cold_start": cold,
            "clicked": False,
            "clicked_position": None,
        }
        self.impression_index[request_id] = record
        self.impression_order.append(request_id)
        if len(self.impression_index) > 8000:
            oldest = self.impression_order.popleft() if self.impression_order else None
            self.impression_index.pop(oldest, None)
        self.pending_impressions.append(record)
        self.experiment[variant]["impressions"] += 1

        session_state = self.store.sessions.get(session_id)
        return {
            "request_id": request_id,
            "user_id": request.user_id,
            "session_id": session_id,
            "variant": variant,
            "surface": request.surface,
            "cold_start": cold,
            "items": items,
            "stage_counts": stage_counts,
            "timings_ms": {
                "retrieval": round(retrieval_ms, 3),
                "ranking": round(ranking_ms, 3),
                "total": round(total_ms, 3),
            },
            "session_signal": {
                "events": session_state.events if session_state else 0,
                "items": len(session_items),
                "top_categories": dict(sorted((session_state.categories if session_state else {}).items(), key=lambda kv: -kv[1])[:3]),
                "vector_norm": round(float(np.linalg.norm(session_state.vector)), 4) if session_state else 0.0,
            },
        }

    # ---- ingest ---------------------------------------------------------
    async def ingest(self, event) -> dict[str, Any]:
        assert self.store
        started = time.perf_counter()
        session_id = event.session_id or event.user_id
        item = self.catalog.get(event.item_id)
        variant = None

        self.store.observe(
            user_id=event.user_id,
            session_id=session_id,
            item_id=event.item_id,
            event=event.event,
            item_meta=item,
        )
        events_total.labels(event=event.event).inc()
        self.events_seen += 1

        if event.request_id:
            record = self.impression_index.get(event.request_id)
            if record is not None:
                variant = record["variant"]
                if event.event in ("click", "add_to_cart", "purchase") and not record["clicked"]:
                    record["clicked"] = True
                    try:
                        record["clicked_position"] = record["item_ids"].index(event.item_id)
                    except ValueError:
                        record["clicked_position"] = None
                    self.experiment[variant]["clicks"] += 1
                    clicks_total.labels(variant=variant).inc()
                if event.event == "purchase":
                    self.experiment[variant]["purchases"] += 1
                    self.experiment[variant]["revenue"] += float(item["price"]) if item else 0.0

        self.pending_interactions.append(
            {
                "user_id": event.user_id,
                "session_id": session_id,
                "item_id": event.item_id,
                "event": event.event,
                "weight": {"view": 1.0, "click": 3.0, "add_to_cart": 5.0, "purchase": 8.0}.get(event.event, 1.0),
                "position": event.position,
                "request_id": event.request_id,
                "variant": variant,
                "source": "api",
            }
        )

        elapsed_us = (time.perf_counter() - started) * 1_000_000
        state = self.store.sessions.get(session_id)
        return {
            "accepted": True,
            "event": event.event,
            "session_items": len(state.items) if state else 0,
            "feature_update_us": round(elapsed_us, 1),
            "profile_strength": round(float(np.linalg.norm(self.store.user_vectors.get(event.user_id, np.zeros(1)))), 4),
        }

    # ---- persistence ----------------------------------------------------
    async def flush(self) -> dict[str, int]:
        async with self._lock:
            interactions, self.pending_interactions = self.pending_interactions, []
            impressions, self.pending_impressions = self.pending_impressions, []
        if not interactions and not impressions:
            return {"interactions": 0, "impressions": 0}

        async with session_scope() as session:
            if interactions:
                session.add_all([Interaction(**row) for row in interactions])
            for record in impressions:
                session.add(
                    Impression(
                        request_id=record["request_id"],
                        user_id=record["user_id"],
                        session_id=record["session_id"],
                        surface=record["surface"],
                        variant=record["variant"],
                        item_ids=record["item_ids"],
                        scores=record["scores"],
                        stage_counts=record["stage_counts"],
                        retrieval_ms=record["retrieval_ms"],
                        ranking_ms=record["ranking_ms"],
                        total_ms=record["total_ms"],
                        cold_start=record["cold_start"],
                        clicked=record["clicked"],
                        clicked_position=record["clicked_position"],
                    )
                )
        feature_lag.set(max(0.0, time.time() - (self.store.last_event_at if self.store else time.time())))
        return {"interactions": len(interactions), "impressions": len(impressions)}

    async def persist_features(self) -> dict[str, int]:
        """Snapshot the online feature store so a restart is warm, not cold."""
        assert self.store
        profiles = 0
        async with session_scope() as session:
            for user_id, vector in list(self.store.user_vectors.items())[:5000]:
                row = await session.get(UserProfile, user_id)
                payload = [round(float(v), 5) for v in vector]
                categories = dict(sorted(self.store.user_categories.get(user_id, {}).items(), key=lambda kv: -kv[1])[:5])
                if row is None:
                    session.add(UserProfile(user_id=user_id, vector=payload, events=1, top_categories=categories))
                else:
                    row.vector = payload
                    row.top_categories = categories
                    row.events += 1
                    row.updated_at = utcnow()
                profiles += 1

            for item_id in list(self.store.trend.keys())[:5000]:
                row = await session.get(ItemStat, item_id)
                values = {
                    "views": int(self.store.item_views.get(item_id, 0)),
                    "clicks": int(self.store.item_clicks.get(item_id, 0)),
                    "trend_score": float(self.store.trend.get(item_id, 0.0)),
                }
                if row is None:
                    session.add(ItemStat(item_id=item_id, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.updated_at = utcnow()

            top_pairs = sorted(
                ((a, b, w) for a, neighbours in self.store.covisit.items() for b, w in neighbours.items()),
                key=lambda triple: -triple[2],
            )[:4000]
            for item_a, item_b, weight in top_pairs:
                row = await session.get(CoVisit, (item_a, item_b))
                if row is None:
                    session.add(CoVisit(item_a=item_a, item_b=item_b, weight=float(weight)))
                else:
                    row.weight = float(weight)
                    row.updated_at = utcnow()
        return {"profiles": profiles, "covisit_pairs": len(top_pairs)}

    # ---- analytics ------------------------------------------------------
    @staticmethod
    def _percentile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
        return round(ordered[index], 3)

    def experiment_report(self) -> dict[str, Any]:
        control = self.experiment.get(settings.control_variant, {})
        treatment = self.experiment.get(settings.treatment_variant, {})

        def ctr(row: dict[str, float]) -> float | None:
            impressions = row.get("impressions", 0)
            return row.get("clicks", 0) / impressions if impressions else None

        control_ctr, treatment_ctr = ctr(control), ctr(treatment)
        uplift = None
        z_score = None
        if control_ctr and treatment_ctr:
            uplift = treatment_ctr / control_ctr - 1
            n1, n2 = control.get("impressions", 0), treatment.get("impressions", 0)
            x1, x2 = control.get("clicks", 0), treatment.get("clicks", 0)
            pooled = (x1 + x2) / (n1 + n2)
            standard_error = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2)) if n1 and n2 and 0 < pooled < 1 else 0
            z_score = (treatment_ctr - control_ctr) / standard_error if standard_error else None

        return {
            "experiment": settings.experiment_name,
            "control": {
                "variant": settings.control_variant,
                "impressions": int(control.get("impressions", 0)),
                "clicks": int(control.get("clicks", 0)),
                "purchases": int(control.get("purchases", 0)),
                "revenue": round(control.get("revenue", 0.0), 2),
                "ctr": round(control_ctr, 4) if control_ctr is not None else None,
            },
            "treatment": {
                "variant": settings.treatment_variant,
                "impressions": int(treatment.get("impressions", 0)),
                "clicks": int(treatment.get("clicks", 0)),
                "purchases": int(treatment.get("purchases", 0)),
                "revenue": round(treatment.get("revenue", 0.0), 2),
                "ctr": round(treatment_ctr, 4) if treatment_ctr is not None else None,
            },
            "ctr_uplift": round(uplift, 4) if uplift is not None else None,
            "z_score": round(z_score, 2) if z_score is not None else None,
            "significant_95": bool(z_score is not None and abs(z_score) >= 1.96),
        }

    def stats(self) -> dict[str, Any]:
        assert self.store
        now = time.time()
        recent = [t for t in self.request_times if now - t <= 60]
        offline = (self.artifacts.metrics or {}).get("offline", {})
        return {
            "generated_at": iso(utcnow()),
            "platform": {
                "version": settings.version,
                "env": settings.app_env,
                "database": "sqlite" if settings.is_sqlite else "postgres",
                "uptime_s": round(now - self.started_at, 1),
                "model_loaded": self.artifacts.loaded,
            },
            "index": {
                "items": len(self.artifacts.item_ids) or len(self.catalog),
                "factors": self.artifacts.factors,
                "ivf_clusters": len(self.artifacts.centroids) if self.artifacts.centroids is not None else 0,
                "probe_clusters": settings.ann_probe_clusters,
                "known_users": len(self.artifacts.user_index),
            },
            "traffic": {
                "served_total": self.served_total,
                "events_total": self.events_seen,
                "qps_60s": round(len(recent) / 60, 2),
                "active_sessions": len(self.store.sessions),
                "cold_start_rate": round(
                    sum(1 for record in self.impression_index.values() if record["cold_start"]) / max(1, len(self.impression_index)), 4
                ),
            },
            "latency_ms": {
                "p50": self._percentile(list(self.latency_samples), 0.50),
                "p95": self._percentile(list(self.latency_samples), 0.95),
                "p99": self._percentile(list(self.latency_samples), 0.99),
                "retrieval_p95": self._percentile(list(self.retrieval_samples), 0.95),
                "ranking_p95": self._percentile(list(self.ranking_samples), 0.95),
                "samples": len(self.latency_samples),
            },
            "features": {
                "session_vectors": len(self.store.sessions),
                "user_vectors": len(self.store.user_vectors),
                "covisit_pairs": sum(len(v) for v in self.store.covisit.values()),
                "feature_lag_s": round(max(0.0, now - self.store.last_event_at), 3) if self.store.last_event_at else None,
                "pending_writes": len(self.pending_interactions) + len(self.pending_impressions),
            },
            "experiment": self.experiment_report(),
            "offline_metrics": offline,
            "model": {
                "trained_at": (self.artifacts.metrics or {}).get("trained_at"),
                "ranker": (self.artifacts.metrics or {}).get("ranker", {}),
                "ndcg_lift_over_popularity": (self.artifacts.metrics or {}).get("ndcg_lift_over_popularity"),
                "als_seconds": (self.artifacts.metrics or {}).get("als_seconds"),
            },
        }

    def trending_items(self, limit: int = 12) -> list[dict[str, Any]]:
        assert self.store
        return [
            {**self.catalog[item_id], "trend_score": round(self.store.trend.get(item_id, 0.0), 3)}
            for item_id in self.store.trending(limit)
            if item_id in self.catalog
        ]

    # ---- traffic simulation --------------------------------------------
    async def simulate(self, users: int, steps: int, surface: str, click_noise: float) -> dict[str, Any]:
        """Drive synthetic sessions through the real serving path.

        Simulated shoppers have a hidden taste vector and click what they like,
        which is what makes the online CTR comparison between variants
        meaningful. Everything goes through the same API code as real traffic.
        """
        from .schemas import EventRequest, RecommendRequest

        rng = random.Random(1234 + users * steps)
        # Unit vectors so affinity is a cosine in [-1, 1] and the click model is
        # actually discriminating rather than saturated.
        latent = self.artifacts.unit_factors
        served = clicks = purchases = 0
        started = time.perf_counter()

        for index in range(users):
            user_id = f"sim_{rng.randrange(10**6):06d}"
            session_id = f"sim_ses_{index}_{int(time.time())}"
            # A hidden taste vector: the average of two random catalogue items.
            seeds = rng.sample(list(self.catalog.keys()), k=2)
            taste = np.zeros(self.artifacts.factors, dtype=np.float32)
            for seed in seeds:
                position = self.artifacts.item_index.get(seed)
                if position is not None and latent is not None:
                    taste += latent[position]
            norm = float(np.linalg.norm(taste)) or 1.0
            taste /= norm

            for _step in range(steps):
                response = await self.recommend(
                    RecommendRequest(user_id=user_id, session_id=session_id, surface=surface, limit=12)
                )
                served += 1
                for item in response["items"]:
                    position = self.artifacts.item_index.get(item["item_id"])
                    affinity = float(latent[position] @ taste) if position is not None and latent is not None else 0.0
                    probability = 1 / (1 + math.exp(-(6.0 * affinity - 2.4 - 0.10 * item["rank"])))
                    probability = (1 - click_noise) * probability + click_noise * 0.06
                    if rng.random() < probability:
                        await self.ingest(
                            EventRequest(
                                user_id=user_id,
                                session_id=session_id,
                                item_id=item["item_id"],
                                event="click",
                                request_id=response["request_id"],
                                position=item["rank"] - 1,
                            )
                        )
                        clicks += 1
                        if rng.random() < 0.12:
                            await self.ingest(
                                EventRequest(
                                    user_id=user_id,
                                    session_id=session_id,
                                    item_id=item["item_id"],
                                    event="purchase",
                                    request_id=response["request_id"],
                                )
                            )
                            purchases += 1
                        break  # one click per impression, like a real rail

        elapsed = time.perf_counter() - started
        await self.flush()
        return {
            "users": users,
            "steps": steps,
            "requests_served": served,
            "clicks": clicks,
            "purchases": purchases,
            "seconds": round(elapsed, 2),
            "requests_per_second": round(served / elapsed, 1) if elapsed else None,
            "experiment": self.experiment_report(),
        }


service = RecommendationService()


async def background_loop(logger) -> None:
    """Flush buffers, snapshot features, expire sessions."""
    tick = 0
    while True:
        await asyncio.sleep(settings.feature_flush_interval_s)
        tick += 1
        try:
            written = await service.flush()
            if tick % 15 == 0:
                expired = service.store.expire() if service.store else 0
                snapshot = await service.persist_features()
                log_event(
                    logger,
                    "info",
                    "background maintenance",
                    expired_sessions=expired,
                    **snapshot,
                    **written,
                )
        except Exception as exc:  # pragma: no cover - keep the loop alive
            log_event(logger, "error", "background flush failed", error=str(exc))
