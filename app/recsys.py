"""The recommender itself: streaming features, two-stage retrieval, ranking.

Layout of a request (target: single-digit milliseconds at p95):

    query vector  = user factor (or session EMA for cold users)
    stage 1       = IVF probe over item factors  +  co-visitation  +  trending
    stage 2       = gradient-boosted ranker over 10 features
    post-process  = MMR diversification + per-category cap

Streaming features live in memory and are updated on the write path, so a click
changes the next response immediately; the durable copy is flushed in batches.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .config import settings
from .observability import candidates_gauge, index_size, model_up, sessions_gauge, stage_latency


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------
@dataclass
class Artifacts:
    item_ids: list[str] = field(default_factory=list)
    item_index: dict[str, int] = field(default_factory=dict)
    user_index: dict[str, int] = field(default_factory=dict)
    item_factors: np.ndarray | None = None
    user_factors: np.ndarray | None = None
    centroids: np.ndarray | None = None
    assignments: np.ndarray | None = None
    cluster_members: dict[int, np.ndarray] = field(default_factory=dict)
    ranker: Any = None
    aggregates: dict = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    price_log_mean: float = 0.0
    price_log_std: float = 1.0
    factors: int = 32
    metrics: dict = field(default_factory=dict)
    loaded: bool = False

    @property
    def unit_factors(self) -> np.ndarray:
        return self._unit

    def finalise(self) -> None:
        vectors = self.item_factors if self.item_factors is not None else np.zeros((0, self.factors), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
        self._unit = (vectors / norms).astype(np.float32)
        if self.assignments is not None:
            self.cluster_members = {
                int(cluster): np.where(self.assignments == cluster)[0]
                for cluster in np.unique(self.assignments)
            }


def load_artifacts(directory: str | None = None) -> Artifacts:
    base = Path(directory or settings.artifacts_dir)
    art = Artifacts()
    index_file = base / "index.json"
    if not index_file.exists():
        art.finalise()
        model_up.set(0)
        return art

    meta = json.loads(index_file.read_text(encoding="utf-8"))
    art.item_ids = meta["item_ids"]
    art.item_index = {item_id: i for i, item_id in enumerate(art.item_ids)}
    art.user_index = {user_id: i for i, user_id in enumerate(meta.get("user_ids", []))}
    art.factors = int(meta.get("factors", 32))
    art.feature_names = meta.get("feature_names", [])
    art.price_log_mean = float(meta.get("price_log_mean", 0.0))
    art.price_log_std = float(meta.get("price_log_std", 1.0))

    art.item_factors = np.load(base / "item_factors.npy").astype(np.float32)
    user_path = base / "user_factors.npy"
    if user_path.exists():
        art.user_factors = np.load(user_path).astype(np.float32)
    centroids = base / "ivf_centroids.npy"
    if centroids.exists():
        art.centroids = np.load(centroids).astype(np.float32)
        art.assignments = np.load(base / "ivf_assignments.npy")
    aggregates = base / "aggregates.json"
    if aggregates.exists():
        art.aggregates = json.loads(aggregates.read_text(encoding="utf-8"))
    metrics = base / "metrics.json"
    if metrics.exists():
        art.metrics = json.loads(metrics.read_text(encoding="utf-8"))

    try:
        import joblib

        ranker_path = base / "ranker.joblib"
        if ranker_path.exists():
            art.ranker = joblib.load(ranker_path)
    except Exception:
        art.ranker = None

    art.loaded = art.item_factors is not None and art.ranker is not None
    art.finalise()
    index_size.set(len(art.item_ids))
    model_up.set(1 if art.loaded else 0)
    return art


# ---------------------------------------------------------------------------
# streaming feature store
# ---------------------------------------------------------------------------
EVENT_WEIGHTS = {"view": 1.0, "click": 3.0, "add_to_cart": 5.0, "purchase": 8.0}
POSITIVE_EVENTS = ("click", "add_to_cart", "purchase")


@dataclass
class SessionState:
    vector: np.ndarray
    items: deque[str]
    categories: dict[str, float]
    brands: dict[str, float]
    updated_at: float
    events: int = 0


class FeatureStore:
    """In-memory online features with exponential time decay.

    Everything here is derived state: losing it costs recommendation quality for
    a few seconds, never correctness, and it can be rebuilt from the durable
    interaction log.
    """

    def __init__(self, artifacts: Artifacts) -> None:
        self.art = artifacts
        self.sessions: dict[str, SessionState] = {}
        self.user_vectors: dict[str, np.ndarray] = {}
        self.user_categories: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.user_brands: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.item_clicks: dict[str, float] = defaultdict(float)
        self.item_views: dict[str, float] = defaultdict(float)
        self.trend: dict[str, float] = defaultdict(float)
        self.trend_stamp: dict[str, float] = {}
        self.covisit: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.last_event_at: float = 0.0
        self._seed_from_artifacts()

    def _seed_from_artifacts(self) -> None:
        agg = self.art.aggregates or {}
        for item_id, count in (agg.get("clicks") or {}).items():
            self.item_clicks[item_id] = float(count)
        for item_id, count in (agg.get("views") or {}).items():
            self.item_views[item_id] = float(count)
        for item_id, score in (agg.get("popularity") or {}).items():
            self.trend[item_id] = float(score) * 100
        for item_a, neighbours in (agg.get("covisit") or {}).items():
            for item_b, weight in neighbours.items():
                self.covisit[item_a][item_b] = float(weight)
        for user_id, categories in (agg.get("user_categories") or {}).items():
            for category, weight in categories.items():
                self.user_categories[user_id][category] = float(weight)
        for user_id, brands in (agg.get("user_brands") or {}).items():
            for brand, weight in brands.items():
                self.user_brands[user_id][brand] = float(weight)

    # ---- writes ---------------------------------------------------------
    def observe(self, *, user_id: str, session_id: str, item_id: str, event: str, item_meta: dict | None) -> None:
        now = time.time()
        weight = EVENT_WEIGHTS.get(event, 1.0)
        self.last_event_at = now

        if event == "view":
            self.item_views[item_id] += 1.0
        if event in POSITIVE_EVENTS:
            self.item_clicks[item_id] += 1.0
            self._bump_trend(item_id, weight, now)

        vector = self._item_vector(item_id)
        session = self.sessions.get(session_id)
        if session is None:
            session = SessionState(
                vector=np.zeros(self.art.factors, dtype=np.float32),
                items=deque(maxlen=24),
                categories=defaultdict(float),
                brands=defaultdict(float),
                updated_at=now,
            )
            self.sessions[session_id] = session

        if vector is not None and event in POSITIVE_EVENTS:
            decay = settings.session_decay
            session.vector = decay * session.vector + (1 - decay) * weight * vector
            profile = self.user_vectors.get(user_id)
            self.user_vectors[user_id] = (
                0.94 * profile + 0.06 * weight * vector if profile is not None else weight * vector.copy()
            )
        if event in POSITIVE_EVENTS and item_id not in session.items:
            # Co-visitation: the last few positives in this session vote for each other.
            for previous in list(session.items)[-6:]:
                self.covisit[previous][item_id] += 1.0
                self.covisit[item_id][previous] += 1.0
        if event in POSITIVE_EVENTS:
            session.items.append(item_id)
        session.events += 1
        session.updated_at = now

        if item_meta and event in POSITIVE_EVENTS:
            session.categories[item_meta["category"]] += weight
            session.brands[item_meta["brand"]] += weight
            self.user_categories[user_id][item_meta["category"]] += weight
            self.user_brands[user_id][item_meta["brand"]] += weight

        sessions_gauge.set(len(self.sessions))

    def _bump_trend(self, item_id: str, weight: float, now: float) -> None:
        previous = self.trend_stamp.get(item_id, now)
        elapsed = max(0.0, now - previous)
        decay = 0.5 ** (elapsed / settings.trending_halflife_s)
        self.trend[item_id] = self.trend[item_id] * decay + weight
        self.trend_stamp[item_id] = now

    def expire(self) -> int:
        cutoff = time.time() - settings.session_ttl_s
        stale = [key for key, state in self.sessions.items() if state.updated_at < cutoff]
        for key in stale:
            self.sessions.pop(key, None)
        sessions_gauge.set(len(self.sessions))
        return len(stale)

    # ---- reads ----------------------------------------------------------
    def _item_vector(self, item_id: str) -> np.ndarray | None:
        position = self.art.item_index.get(item_id)
        if position is None or self.art.item_factors is None:
            return None
        return self.art.item_factors[position]

    def session_items(self, session_id: str) -> list[str]:
        state = self.sessions.get(session_id)
        return list(state.items) if state else []

    def query_vector(self, user_id: str, session_id: str) -> tuple[np.ndarray, bool]:
        """Blend the offline user factor with the live session vector."""
        dim = self.art.factors
        offline = None
        position = self.art.user_index.get(user_id)
        if position is not None and self.art.user_factors is not None and position < len(self.art.user_factors):
            offline = self.art.user_factors[position]
        online = self.user_vectors.get(user_id)
        session = self.sessions.get(session_id)
        session_vector = session.vector if session is not None else None

        parts: list[tuple[np.ndarray, float]] = []
        if offline is not None:
            parts.append((offline, 1.0))
        if online is not None:
            parts.append((online, 0.6))
        if session_vector is not None and np.any(session_vector):
            parts.append((session_vector, 1.4))  # recency wins

        if not parts:
            return np.zeros(dim, dtype=np.float32), True

        stacked = np.zeros(dim, dtype=np.float32)
        for vector, weight in parts:
            norm = np.linalg.norm(vector)
            if norm > 1e-9:
                stacked += weight * (vector / norm)
        cold = offline is None and (session_vector is None or not np.any(session_vector))
        return stacked.astype(np.float32), cold

    def trending(self, limit: int) -> list[str]:
        now = time.time()
        scored = []
        for item_id, score in self.trend.items():
            elapsed = now - self.trend_stamp.get(item_id, now)
            scored.append((score * 0.5 ** (elapsed / settings.trending_halflife_s), item_id))
        scored.sort(reverse=True)
        return [item_id for _score, item_id in scored[:limit]]

    def ctr(self, item_id: str) -> float:
        views = self.item_views.get(item_id, 0.0)
        return self.item_clicks.get(item_id, 0.0) / views if views > 0 else 0.0

    def popularity(self, item_id: str) -> float:
        total = sum(self.item_clicks.values()) or 1.0
        return self.item_clicks.get(item_id, 0.0) / total


# ---------------------------------------------------------------------------
# stage 1: retrieval
# ---------------------------------------------------------------------------
class Retriever:
    def __init__(self, artifacts: Artifacts, store: FeatureStore) -> None:
        self.art = artifacts
        self.store = store

    def ann(self, query: np.ndarray, limit: int) -> list[str]:
        if self.art.item_factors is None or not len(self.art.item_ids):
            return []
        norm = np.linalg.norm(query)
        if norm < 1e-9:
            return []
        unit_query = (query / norm).astype(np.float32)

        if self.art.centroids is not None and self.art.cluster_members:
            probes = min(settings.ann_probe_clusters, len(self.art.centroids))
            cluster_scores = self.art.centroids @ unit_query
            best = np.argsort(-cluster_scores)[:probes]
            member_ids = np.concatenate([self.art.cluster_members[int(c)] for c in best]) if len(best) else np.array([], dtype=int)
            if member_ids.size == 0:
                member_ids = np.arange(len(self.art.item_ids))
        else:
            member_ids = np.arange(len(self.art.item_ids))

        scores = self.art.item_factors[member_ids] @ query
        top = member_ids[np.argsort(-scores)[:limit]]
        return [self.art.item_ids[int(i)] for i in top]

    def covisit(self, session_items: Iterable[str], limit: int) -> list[str]:
        scores: dict[str, float] = defaultdict(float)
        recent = list(session_items)[-6:]
        for weight_index, item_id in enumerate(reversed(recent)):
            recency = 0.85 ** weight_index
            for neighbour, weight in (self.store.covisit.get(item_id) or {}).items():
                if neighbour not in recent:
                    scores[neighbour] += weight * recency
        ordered = sorted(scores.items(), key=lambda kv: -kv[1])
        return [item_id for item_id, _ in ordered[:limit]]

    def trending(self, limit: int) -> list[str]:
        return self.store.trending(limit)

    def candidates(self, query: np.ndarray, session_items: list[str]) -> tuple[list[str], dict[str, int]]:
        started = time.perf_counter()
        ann = self.ann(query, settings.candidates_ann)
        covisit = self.covisit(session_items, settings.candidates_covisit)
        trend = self.trending(settings.candidates_trending)
        merged = list(dict.fromkeys([*ann, *covisit, *trend]))
        merged = [item_id for item_id in merged if item_id in self.art.item_index]
        stage_latency.labels(stage="retrieval").observe(time.perf_counter() - started)
        for source, size in (("ann", len(ann)), ("covisit", len(covisit)), ("trending", len(trend))):
            candidates_gauge.labels(source=source).observe(size)
        return merged, {"ann": len(ann), "covisit": len(covisit), "trending": len(trend), "merged": len(merged)}


# ---------------------------------------------------------------------------
# stage 2: ranking
# ---------------------------------------------------------------------------
class Ranker:
    """Feature extraction must mirror ml/train.py exactly - same order, same maths."""

    def __init__(self, artifacts: Artifacts, store: FeatureStore, catalog: dict[str, dict]) -> None:
        self.art = artifacts
        self.store = store
        self.catalog = catalog

    def _normalise(self, counter: dict[str, float]) -> dict[str, float]:
        total = sum(counter.values()) or 1.0
        return {key: value / total for key, value in counter.items()}

    def features(
        self, user_id: str, candidate_ids: list[str], query: np.ndarray, session_items: list[str], cold: bool
    ) -> np.ndarray:
        factors = self.art.item_factors
        session_vectors = [factors[self.art.item_index[i]] for i in session_items if i in self.art.item_index]
        session_vector = np.mean(session_vectors, axis=0) if session_vectors else None
        user_cats = self._normalise(dict(self.store.user_categories.get(user_id, {})))
        user_brands = self._normalise(dict(self.store.user_brands.get(user_id, {})))

        covisit_scores: dict[str, float] = defaultdict(float)
        for item_id in session_items[-6:]:
            for neighbour, weight in (self.store.covisit.get(item_id) or {}).items():
                covisit_scores[neighbour] += weight

        rows = np.zeros((len(candidate_ids), 10), dtype=np.float32)
        for row_index, item_id in enumerate(candidate_ids):
            item = self.catalog.get(item_id)
            if item is None:
                continue
            vector = factors[self.art.item_index[item_id]]
            rows[row_index] = (
                float(query @ vector),
                float(session_vector @ vector) if session_vector is not None else 0.0,
                float(math.log1p(covisit_scores.get(item_id, 0.0))),
                float(self.store.popularity(item_id) * 100),
                float(self.store.ctr(item_id)),
                float((math.log(max(1.0, item["price"])) - self.art.price_log_mean) / (self.art.price_log_std + 1e-9)),
                float(item["rating"]),
                float(user_cats.get(item["category"], 0.0)),
                float(user_brands.get(item["brand"], 0.0)),
                1.0 if cold else 0.0,
            )
        return rows

    def score(self, user_id: str, candidate_ids: list[str], query: np.ndarray, session_items: list[str], cold: bool) -> np.ndarray:
        if not candidate_ids:
            return np.array([], dtype=np.float32)
        started = time.perf_counter()
        matrix = self.features(user_id, candidate_ids, query, session_items, cold)
        if self.art.ranker is not None:
            scores = self.art.ranker.predict_proba(matrix)[:, 1].astype(np.float32)
        else:
            # No trained ranker: fall back to retrieval score + popularity prior.
            scores = (matrix[:, 0] + 0.35 * matrix[:, 3] + 0.2 * matrix[:, 2]).astype(np.float32)
        stage_latency.labels(stage="ranking").observe(time.perf_counter() - started)
        return scores


# ---------------------------------------------------------------------------
# post-processing
# ---------------------------------------------------------------------------
def diversify(
    candidate_ids: list[str],
    scores: np.ndarray,
    artifacts: Artifacts,
    catalog: dict[str, dict],
    limit: int,
    lambda_: float | None = None,
    category_cap: int | None = None,
    exclude: set[str] | None = None,
) -> list[tuple[str, float]]:
    """Maximal marginal relevance with a per-category cap.

    Pure relevance collapses into one category; this keeps the rail useful.
    """
    lam = settings.mmr_lambda if lambda_ is None else lambda_
    cap = settings.category_cap if category_cap is None else category_cap
    excluded = exclude or set()
    order = np.argsort(-scores)
    pool = [(candidate_ids[i], float(scores[i])) for i in order if candidate_ids[i] not in excluded]
    if not pool:
        return []

    unit = artifacts.unit_factors
    chosen: list[tuple[str, float]] = []
    chosen_vectors: list[np.ndarray] = []
    category_counts: dict[str, int] = defaultdict(int)

    while pool and len(chosen) < limit:
        best_index, best_value = 0, -1e9
        for position, (item_id, score) in enumerate(pool[: limit * 4]):
            item = catalog.get(item_id)
            if item and category_counts[item["category"]] >= cap:
                continue
            penalty = 0.0
            if chosen_vectors:
                vector = unit[artifacts.item_index[item_id]]
                penalty = max(float(vector @ other) for other in chosen_vectors)
            value = lam * score - (1 - lam) * penalty
            if value > best_value:
                best_index, best_value = position, value
        item_id, score = pool.pop(best_index)
        item = catalog.get(item_id)
        if item:
            category_counts[item["category"]] += 1
        chosen.append((item_id, score))
        chosen_vectors.append(unit[artifacts.item_index[item_id]])
    return chosen
