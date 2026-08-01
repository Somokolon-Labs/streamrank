"""Train the two-stage recommender and measure it against baselines.

Stage 1 (retrieval): implicit-feedback ALS matrix factorisation, plus an IVF
index (k-means cells) so retrieval stays sub-linear as the catalogue grows.
Stage 2 (ranking): gradient-boosted / logistic ranker over retrieval score,
popularity, co-visitation, price, rating and session-affinity features.

The evaluation is chronological: fit on the oldest 70% of the log, train the
ranker on the next 15%, and score the final 15% that neither model has seen.

    python ml/train.py --factors 32 --iterations 18
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "catalog.json"
LOG = ROOT / "data" / "interactions.csv"
ARTIFACTS = ROOT / "ml" / "artifacts"

EVENT_WEIGHTS = {"view": 1.0, "click": 3.0, "add_to_cart": 5.0, "purchase": 8.0}
POSITIVE_EVENTS = ("click", "add_to_cart", "purchase")
FEATURE_NAMES = [
    "mf_score",
    "session_affinity",
    "covisit",
    "popularity",
    "item_ctr",
    "price_z",
    "rating",
    "category_affinity",
    "brand_affinity",
    "is_cold_user",
]


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------
def load() -> tuple[list[dict], list[dict]]:
    if not CATALOG.exists() or not LOG.exists():
        raise SystemExit("missing data - run: python ml/generate_data.py")
    items = json.loads(CATALOG.read_text(encoding="utf-8"))
    with LOG.open("r", encoding="utf-8", newline="") as handle:
        rows = [
            {
                "user_id": row["user_id"],
                "session_id": row["session_id"],
                "item_id": row["item_id"],
                "event": row["event"],
                "position": int(row["position"]),
                "ts": float(row["ts"]),
            }
            for row in csv.DictReader(handle)
        ]
    rows.sort(key=lambda r: r["ts"])
    return items, rows


def split(rows: list[dict], fit: float = 0.70, rank: float = 0.15) -> tuple[list[dict], list[dict], list[dict]]:
    n = len(rows)
    a, b = int(n * fit), int(n * (fit + rank))
    return rows[:a], rows[a:b], rows[b:]


# ---------------------------------------------------------------------------
# stage 1: implicit ALS
# ---------------------------------------------------------------------------
def train_als(
    rows: list[dict],
    user_index: dict[str, int],
    item_index: dict[str, int],
    factors: int,
    iterations: int,
    reg: float = 0.06,
    alpha: float = 22.0,
    seed: int = 7,
) -> tuple[np.ndarray, np.ndarray]:
    n_users, n_items = len(user_index), len(item_index)
    weights = np.zeros((n_users, n_items), dtype=np.float32)
    for row in rows:
        u, i = user_index.get(row["user_id"]), item_index.get(row["item_id"])
        if u is None or i is None:
            continue
        weights[u, i] += EVENT_WEIGHTS.get(row["event"], 1.0)

    preference = (weights > 0).astype(np.float32)
    confidence = 1.0 + alpha * np.log1p(weights)

    rng = np.random.default_rng(seed)
    U = rng.normal(0, 0.05, (n_users, factors)).astype(np.float32)
    V = rng.normal(0, 0.05, (n_items, factors)).astype(np.float32)
    eye = np.eye(factors, dtype=np.float32) * reg

    for _ in range(iterations):
        VtV = V.T @ V
        for u in range(n_users):
            cu = confidence[u]
            active = cu > 1.0
            if not active.any():
                U[u] = 0
                continue
            Va = V[active]
            A = VtV + Va.T @ (Va * (cu[active] - 1.0)[:, None]) + eye
            b = Va.T @ (cu[active] * preference[u, active])
            U[u] = np.linalg.solve(A, b)

        UtU = U.T @ U
        for i in range(n_items):
            ci = confidence[:, i]
            active = ci > 1.0
            if not active.any():
                V[i] = 0
                continue
            Ua = U[active]
            A = UtU + Ua.T @ (Ua * (ci[active] - 1.0)[:, None]) + eye
            b = Ua.T @ (ci[active] * preference[active, i])
            V[i] = np.linalg.solve(A, b)

    return U, V


def build_ivf(V: np.ndarray, seed: int = 7) -> dict:
    """K-means cells over item vectors: retrieval probes a few cells, not all items."""
    n_items = V.shape[0]
    n_clusters = max(2, min(32, int(np.sqrt(n_items))))
    norms = np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
    unit = V / norms
    kmeans = KMeans(n_clusters=n_clusters, n_init=6, random_state=seed).fit(unit)
    return {
        "centroids": kmeans.cluster_centers_.astype(np.float32),
        "assignments": kmeans.labels_.astype(np.int32),
        "clusters": n_clusters,
    }


# ---------------------------------------------------------------------------
# aggregates used as ranking features
# ---------------------------------------------------------------------------
def aggregates(rows: list[dict], items: list[dict]) -> dict:
    views: dict[str, int] = defaultdict(int)
    clicks: dict[str, int] = defaultdict(int)
    covisit: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_session: dict[str, list[str]] = defaultdict(list)
    user_categories: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    user_brands: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    meta = {item["id"]: item for item in items}

    for row in rows:
        item = meta.get(row["item_id"])
        if not item:
            continue
        if row["event"] == "view":
            views[row["item_id"]] += 1
        if row["event"] in POSITIVE_EVENTS:
            clicks[row["item_id"]] += 1
            by_session[row["session_id"]].append(row["item_id"])
            weight = EVENT_WEIGHTS.get(row["event"], 1.0)
            user_categories[row["user_id"]][item["category"]] += weight
            user_brands[row["user_id"]][item["brand"]] += weight

    for session_items in by_session.values():
        unique = list(dict.fromkeys(session_items))[-12:]
        for a in unique:
            for b in unique:
                if a != b:
                    covisit[a][b] += 1.0

    total_clicks = sum(clicks.values()) or 1
    popularity = {item_id: count / total_clicks for item_id, count in clicks.items()}
    ctr = {item_id: clicks.get(item_id, 0) / max(1, views.get(item_id, 0)) for item_id in meta}

    return {
        "views": dict(views),
        "clicks": dict(clicks),
        "popularity": popularity,
        "ctr": ctr,
        "covisit": {a: dict(b) for a, b in covisit.items()},
        "user_categories": {u: dict(c) for u, c in user_categories.items()},
        "user_brands": {u: dict(b) for u, b in user_brands.items()},
    }


def normalise(counter: dict[str, float]) -> dict[str, float]:
    total = sum(counter.values()) or 1.0
    return {key: value / total for key, value in counter.items()}


# ---------------------------------------------------------------------------
# feature extraction (mirrors app/ranking.py exactly)
# ---------------------------------------------------------------------------
def features_for(
    user_id: str,
    candidate_ids: list[str],
    query_vector: np.ndarray,
    session_items: list[str],
    agg: dict,
    meta: dict,
    item_index: dict[str, int],
    V: np.ndarray,
    price_stats: tuple[float, float],
    cold: bool,
) -> np.ndarray:
    price_mean, price_std = price_stats
    session_vectors = [V[item_index[i]] for i in session_items if i in item_index]
    session_vector = np.mean(session_vectors, axis=0) if session_vectors else None
    user_cats = normalise(agg["user_categories"].get(user_id, {}))
    user_brands = normalise(agg["user_brands"].get(user_id, {}))
    covisit_scores: dict[str, float] = defaultdict(float)
    for item in session_items[-6:]:
        for neighbour, weight in (agg["covisit"].get(item) or {}).items():
            covisit_scores[neighbour] += weight

    rows = np.zeros((len(candidate_ids), len(FEATURE_NAMES)), dtype=np.float32)
    for row_index, item_id in enumerate(candidate_ids):
        item = meta[item_id]
        vector = V[item_index[item_id]]
        rows[row_index] = (
            float(query_vector @ vector),
            float(session_vector @ vector) if session_vector is not None else 0.0,
            float(np.log1p(covisit_scores.get(item_id, 0.0))),
            float(agg["popularity"].get(item_id, 0.0) * 100),
            float(agg["ctr"].get(item_id, 0.0)),
            float((np.log(item["price"]) - price_mean) / (price_std + 1e-9)),
            float(item["rating"]),
            float(user_cats.get(item["category"], 0.0)),
            float(user_brands.get(item["brand"], 0.0)),
            1.0 if cold else 0.0,
        )
    return rows


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1 / np.log2(rank + 2) for rank, item in enumerate(ranked[:k]) if item in relevant)
    ideal = sum(1 / np.log2(rank + 2) for rank in range(min(k, len(relevant))))
    return float(dcg / ideal) if ideal else 0.0


def evaluate(
    eval_rows: list[dict],
    items: list[dict],
    U: np.ndarray,
    V: np.ndarray,
    user_index: dict[str, int],
    item_index: dict[str, int],
    agg: dict,
    ranker,
    price_stats: tuple[float, float],
    k: int = 10,
    candidates: int = 100,
) -> dict:
    meta = {item["id"]: item for item in items}
    item_ids = [item["id"] for item in items]
    truth: dict[str, set[str]] = defaultdict(set)
    context: dict[str, list[str]] = defaultdict(list)
    for row in eval_rows:
        if row["event"] in POSITIVE_EVENTS:
            truth[row["user_id"]].add(row["item_id"])
        else:
            context[row["user_id"]].append(row["item_id"])

    popular = sorted(item_ids, key=lambda i: -agg["popularity"].get(i, 0.0))
    scores = {"popularity": [], "mf": [], "two_stage": []}
    recall = {"popularity": [], "mf": [], "two_stage": []}
    covered: set[str] = set()
    evaluated = 0

    for user_id, relevant in truth.items():
        if not relevant:
            continue
        u = user_index.get(user_id)
        cold = u is None
        query = V[[item_index[i] for i in context[user_id] if i in item_index]].mean(axis=0) if cold and context[user_id] else (U[u] if not cold else np.zeros(V.shape[1], dtype=np.float32))
        mf_scores = V @ query
        order = np.argsort(-mf_scores)[:candidates]
        candidate_ids = [item_ids[i] for i in order]
        for item_id in popular[:20]:
            if item_id not in candidate_ids:
                candidate_ids.append(item_id)

        mf_ranked = sorted(candidate_ids, key=lambda i: -float(V[item_index[i]] @ query))
        matrix = features_for(user_id, candidate_ids, query, context[user_id][-8:], agg, meta, item_index, V, price_stats, cold)
        probabilities = ranker.predict_proba(matrix)[:, 1]
        two_stage = [candidate_ids[i] for i in np.argsort(-probabilities)]

        for name, ranked in (("popularity", popular), ("mf", mf_ranked), ("two_stage", two_stage)):
            scores[name].append(ndcg_at_k(ranked, relevant, k))
            hits = len(set(ranked[:k]) & relevant)
            recall[name].append(hits / len(relevant))
        covered.update(two_stage[:k])
        evaluated += 1

    return {
        "users_evaluated": evaluated,
        "ndcg_at_10": {name: round(float(np.mean(values)), 4) for name, values in scores.items()},
        "recall_at_10": {name: round(float(np.mean(values)), 4) for name, values in recall.items()},
        "catalog_coverage_at_10": round(len(covered) / len(item_ids), 4),
    }


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factors", type=int, default=32)
    parser.add_argument("--iterations", type=int, default=18)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    started = time.perf_counter()
    items, rows = load()
    fit_rows, rank_rows, eval_rows = split(rows)
    print(f"log split: fit={len(fit_rows)} ranker={len(rank_rows)} eval={len(eval_rows)}")

    item_ids = [item["id"] for item in items]
    item_index = {item_id: i for i, item_id in enumerate(item_ids)}
    user_ids = sorted({row["user_id"] for row in fit_rows})
    user_index = {user_id: i for i, user_id in enumerate(user_ids)}

    prices = np.log(np.array([item["price"] for item in items], dtype=np.float32))
    price_stats = (float(prices.mean()), float(prices.std()))

    print(f"training ALS: {len(user_ids)} users x {len(item_ids)} items, d={args.factors}")
    als_started = time.perf_counter()
    U, V = train_als(fit_rows, user_index, item_index, args.factors, args.iterations, seed=args.seed)
    als_seconds = time.perf_counter() - als_started
    ivf = build_ivf(V, seed=args.seed)
    agg = aggregates(fit_rows, items)

    # ---- ranker training set from the middle slice -------------------------
    meta = {item["id"]: item for item in items}
    rng = np.random.default_rng(args.seed)
    positives: dict[str, list[str]] = defaultdict(list)
    session_context: dict[str, list[str]] = defaultdict(list)
    for row in rank_rows:
        if row["event"] in POSITIVE_EVENTS:
            positives[row["user_id"]].append(row["item_id"])
        else:
            session_context[row["user_id"]].append(row["item_id"])

    X_rows, y_rows = [], []
    for user_id, liked in positives.items():
        u = user_index.get(user_id)
        cold = u is None
        query = U[u] if not cold else (
            V[[item_index[i] for i in session_context[user_id] if i in item_index]].mean(axis=0)
            if session_context[user_id] else np.zeros(args.factors, dtype=np.float32)
        )
        negatives = [
            item_ids[i] for i in rng.choice(len(item_ids), size=min(len(item_ids), 4 * len(liked)), replace=False)
            if item_ids[i] not in liked
        ]
        candidate_ids = list(dict.fromkeys(liked + negatives))
        matrix = features_for(user_id, candidate_ids, query, session_context[user_id][-8:], agg, meta, item_index, V, price_stats, cold)
        X_rows.append(matrix)
        y_rows.append(np.array([1 if item_id in liked else 0 for item_id in candidate_ids], dtype=np.int8))

    X = np.vstack(X_rows)
    y = np.concatenate(y_rows)
    print(f"ranker training set: {X.shape[0]} pairs, positive rate {y.mean():.3f}")

    ranker = HistGradientBoostingClassifier(
        max_iter=220, learning_rate=0.08, max_depth=6, l2_regularization=1.0, random_state=args.seed
    )
    ranker.fit(X, y)
    probabilities = ranker.predict_proba(X)[:, 1]
    auc = float(roc_auc_score(y, probabilities))
    ap = float(average_precision_score(y, probabilities))
    print(f"ranker fit: train AUC={auc:.4f} AP={ap:.4f}")

    report = evaluate(eval_rows, items, U, V, user_index, item_index, agg, ranker, price_stats)
    print("\noffline evaluation on the unseen final slice")
    for metric, values in report.items():
        print(f"  {metric}: {values}")

    lift = report["ndcg_at_10"]["two_stage"] / max(1e-9, report["ndcg_at_10"]["popularity"]) - 1
    print(f"\nNDCG@10 lift over popularity: {lift * 100:.1f}%")

    # ---- persist ----------------------------------------------------------
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    np.save(ARTIFACTS / "item_factors.npy", V)
    np.save(ARTIFACTS / "user_factors.npy", U)
    np.save(ARTIFACTS / "ivf_centroids.npy", ivf["centroids"])
    np.save(ARTIFACTS / "ivf_assignments.npy", ivf["assignments"])
    joblib.dump(ranker, ARTIFACTS / "ranker.joblib", compress=3)
    (ARTIFACTS / "index.json").write_text(
        json.dumps(
            {
                "item_ids": item_ids,
                "user_ids": user_ids,
                "factors": args.factors,
                "clusters": ivf["clusters"],
                "feature_names": FEATURE_NAMES,
                "price_log_mean": price_stats[0],
                "price_log_std": price_stats[1],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (ARTIFACTS / "aggregates.json").write_text(json.dumps(agg), encoding="utf-8")
    metrics = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "items": len(item_ids),
        "users": len(user_ids),
        "interactions": len(rows),
        "factors": args.factors,
        "als_iterations": args.iterations,
        "als_seconds": round(als_seconds, 2),
        "total_seconds": round(time.perf_counter() - started, 2),
        "ivf_clusters": ivf["clusters"],
        "ranker": {"model": "HistGradientBoosting", "train_auc": round(auc, 4), "train_ap": round(ap, 4)},
        "offline": report,
        "ndcg_lift_over_popularity": round(lift, 4),
    }
    (ARTIFACTS / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"\nartifacts -> {ARTIFACTS}")


if __name__ == "__main__":
    main()
