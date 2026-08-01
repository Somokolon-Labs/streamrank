"""End-to-end smoke test + online experiment run for StreamRank.

    python scripts/smoke.py --api http://127.0.0.1:8200

Verifies: catalog, cold-start serving, that a click changes the very next
response (streaming features), variant assignment stability, latency budget,
and that the A/B report shows the two-stage variant beating popularity.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time

import httpx

with contextlib.suppress(AttributeError, OSError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(f" {'+' if ok else 'x'} {name}{f' - {detail}' if detail else ''}", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8200")
    parser.add_argument("--users", type=int, default=120)
    parser.add_argument("--steps", type=int, default=5)
    args = parser.parse_args()

    client = httpx.Client(base_url=args.api, timeout=180.0)
    print("\nStreamRank smoke test")
    print(f"api: {args.api}\n")

    ready = client.get("/health/ready")
    check("service ready", ready.status_code == 200, f"status {ready.status_code}")
    checks = ready.json().get("checks", {})
    check("catalog loaded", checks.get("catalog_items", 0) >= 50, f"{checks.get('catalog_items')} items")
    check("model artifacts loaded", bool(checks.get("model_loaded")), f"ranker={checks.get('ranker')}")

    # cold start
    session_id = f"ses_smoke_{int(time.time())}"
    cold = client.post(
        "/v1/recommend",
        json={"user_id": "usr_smoke_cold", "session_id": session_id, "limit": 12, "variant": "two-stage"},
    ).json()
    check("cold-start recommendation", len(cold["items"]) == 12, f"cold_start={cold['cold_start']}, variant={cold['variant']}")
    check(
        "latency budget (single request)",
        cold["timings_ms"]["total"] < 120,
        f"total={cold['timings_ms']['total']}ms retrieval={cold['timings_ms']['retrieval']}ms ranking={cold['timings_ms']['ranking']}ms",
    )

    # streaming features: a click must change the next response
    target = cold["items"][3]
    event = client.post(
        "/v1/events",
        json={
            "user_id": "usr_smoke_cold",
            "session_id": session_id,
            "item_id": target["item_id"],
            "event": "click",
            "request_id": cold["request_id"],
            "position": 3,
        },
    ).json()
    check("event ingested", event["accepted"], f"feature update in {event['feature_update_us']}us")

    warm = client.post(
        "/v1/recommend",
        json={"user_id": "usr_smoke_cold", "session_id": session_id, "limit": 12, "variant": "two-stage"},
    ).json()
    before = [item["item_id"] for item in cold["items"]]
    after = [item["item_id"] for item in warm["items"]]
    changed = sum(1 for a, b in zip(before, after) if a != b)
    check(
        "click reshapes the next response",
        changed > 0,
        f"{changed}/12 positions changed, session vector norm {warm['session_signal']['vector_norm']}",
    )
    check(
        "clicked category reinforced",
        target["category"] in (warm["session_signal"].get("top_categories") or {}),
        f"top categories {warm['session_signal'].get('top_categories')}",
    )

    # variant assignment must be stable per user
    variants = {
        client.post("/v1/recommend", json={"user_id": "usr_bucket_test", "session_id": "ses_b", "limit": 4}).json()["variant"]
        for _ in range(5)
    }
    check("variant assignment is sticky", len(variants) == 1, f"variant={variants}")

    # online experiment
    print("\nrunning simulated traffic through the real serving path...")
    simulation = client.post("/v1/simulate", json={"users": args.users, "steps": args.steps}).json()
    report = simulation["experiment"]
    check(
        "simulated traffic served",
        simulation["requests_served"] > 0,
        f"{simulation['requests_served']} requests at {simulation['requests_per_second']}/s, {simulation['clicks']} clicks",
    )
    control, treatment = report["control"], report["treatment"]
    check(
        "both variants received traffic",
        control["impressions"] > 0 and treatment["impressions"] > 0,
        f"control={control['impressions']} treatment={treatment['impressions']}",
    )
    uplift = report.get("ctr_uplift")
    check(
        "two-stage beats popularity on CTR",
        uplift is not None and uplift > 0,
        f"control CTR {control['ctr']} vs treatment CTR {treatment['ctr']} -> uplift {uplift and round(uplift * 100, 1)}% (z={report.get('z_score')})",
    )

    stats = client.get("/v1/stats").json()
    latency = stats["latency_ms"]
    check(
        "p95 latency under 50ms",
        (latency["p95"] or 999) < 50,
        f"p50={latency['p50']}ms p95={latency['p95']}ms p99={latency['p99']}ms over {latency['samples']} requests",
    )
    check(
        "retrieval faster than ranking",
        (latency["retrieval_p95"] or 0) <= (latency["ranking_p95"] or 0) + 5,
        f"retrieval p95={latency['retrieval_p95']}ms ranking p95={latency['ranking_p95']}ms",
    )
    check(
        "streaming features live",
        stats["features"]["session_vectors"] > 0,
        f"{stats['features']['session_vectors']} sessions, {stats['features']['covisit_pairs']} co-visit pairs",
    )

    failures = [row for row in results if not row[0]]
    print(f"\n{len(results) - len(failures)}/{len(results)} checks passed")
    if failures:
        for _, name, detail in failures:
            print(f"  FAILED: {name} ({detail})")
        return 1
    print("recommender behaved as specified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
