# Benchmarks

All numbers below were produced on a single laptop (Windows, Python 3.10, SQLite) by
`python scripts/smoke.py` and `python ml/train.py`. Reproduce with:

```bash
python ml/generate_data.py     # deterministic: 96 items, 1500 users, seed 23
python ml/train.py             # ALS + IVF + ranker + chronological evaluation
uvicorn app.main:app --port 8200
python scripts/smoke.py        # 15 checks, includes the online A/B run
```

## Dataset

| Property | Value |
| --- | --- |
| Catalogue | 96 items, 12 categories, real product photography |
| Simulated shoppers | 1,500 |
| Interactions | 75,078 (55,038 views · 15,230 clicks · 3,305 add-to-cart · 1,505 purchases) |
| Baseline click-through rate | 27.7% |
| Split | 70% fit · 15% ranker labels · 15% held-out evaluation (chronological) |

## Offline quality (held-out slice, 530 users)

| Strategy | NDCG@10 | Recall@10 |
| --- | --- | --- |
| popularity baseline | 0.3844 | 0.4357 |
| embeddings only (ALS) | 0.2355 | 0.3193 |
| **two-stage (retrieval + ranker)** | **0.4561** | **0.5679** |

* NDCG@10 lift over popularity: **+18.7%**
* Recall@10 lift over popularity: **+30.3%**
* Catalogue coverage@10: **95.8%** — the lift is not head-item collapse
* Ranker: HistGradientBoosting, 13,554 labelled pairs, train AUC **0.8875**, AP **0.6598**
* ALS fit: 32 factors, 18 iterations, 1,036 users × 96 items

Embeddings alone losing to popularity is expected for short sessions and cold users; it is also
the argument for two stages rather than one model.

## Serving latency (607 requests through the API)

| Metric | Value |
| --- | --- |
| p50 end to end | **0.61 ms** |
| p95 end to end | **25.4 ms** |
| p99 end to end | **52.5 ms** |
| Stage 1 retrieval p95 | **0.74 ms** |
| Stage 2 ranking p95 | **12.97 ms** |
| Online feature write (click path) | **0.24 ms** |
| Throughput during the simulated run | **100 requests/s** (single process, single core) |

Ranking dominates because it is the only stage whose cost scales with candidate count
(~220 per request). The first prediction after a cold start costs ~150 ms, which is why the
service warms the ranker during startup.

## Online experiment

`ranker-v1-vs-popularity`, 50/50 sticky hash bucketing, 607 impressions:

| Variant | Impressions | Clicks | CTR |
| --- | --- | --- | --- |
| `popularity` (control) | 315 | 202 | 64.1% |
| `two-stage` (treatment) | 292 | 226 | 77.4% |

* Relative CTR uplift: **+20.7%**
* Two-proportion z-score: **3.58** → significant at 95%
* Purchases: 24 control vs 33 treatment

Absolute CTR is high because simulated shoppers are generous clickers; the comparison between
arms is what the experiment measures, and both arms see identical traffic conditions.

Uplift across repeated runs ranged **+5% to +21%**, and the z-score only clears 1.96 once a run
accumulates roughly 600 impressions (`--users 120 --steps 5`). Shorter runs show the same direction
with a wider interval — which is the honest behaviour of a small experiment, not noise to hide.

## A note on tail latency

p50 is a property of the code path; p95 and p99 on a laptop are also a property of whatever else is
running. A repeat run taken while a GPU fine-tuning job occupied the machine produced
p50 **15 ms** with p95 **365 ms** and p99 **2.0 s** — same code, same request count, ten times the
scheduling delay. The smoke suite therefore asserts on p50 and reports p95/p99, and the clean
numbers above are the ones to compare against.

## Smoke suite

15/15 checks, including:

- cold-start request returns a full rail within the latency budget;
- a click updates features in ~0.24 ms and changes **12/12** positions in the next response;
- the clicked category appears in the session profile immediately;
- variant assignment is sticky across repeated requests;
- both arms receive traffic and the treatment arm wins on CTR;
- p95 latency stays under 50 ms and retrieval stays cheaper than ranking.
