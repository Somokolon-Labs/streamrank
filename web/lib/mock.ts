/**
 * Standalone demo mode.
 *
 * This is a real (small) recommender, not a stub: the committed catalogue ships
 * the first eight latent dimensions of each item's trained embedding, so the
 * browser can compute the same cosine retrieval, session EMA and diversification
 * the service does. Only the gradient-boosted ranker is replaced, by a linear
 * blend of the same features.
 */

import catalogData from "./catalog.json";
import modelMetrics from "./model-metrics.json";
import type { CatalogItem, ExperimentReport, RecommendResponse, ScoredItem, SimulateResult, Stats } from "./types";

type RawItem = CatalogItem & { latent: number[] };

const CATALOG = catalogData as RawItem[];
const DIM = CATALOG[0]?.latent?.length ?? 8;
const CONTROL = "popularity";
const TREATMENT = "two-stage";

// Seeded from a recorded run of the real service so the dashboard is meaningful
// before any demo traffic arrives.
const SEED_EXPERIMENT = {
  [CONTROL]: { impressions: 315, clicks: 202, purchases: 24, revenue: 5216.4 },
  [TREATMENT]: { impressions: 292, clicks: 226, purchases: 33, revenue: 7458.9 },
};

function dot(a: number[], b: number[]): number {
  let total = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) total += a[i] * b[i];
  return total;
}

function norm(a: number[]): number {
  return Math.sqrt(dot(a, a)) || 1;
}

function unit(a: number[]): number[] {
  const n = norm(a);
  return a.map((v) => v / n);
}

function hashBucket(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 0xffffffff;
}

class MockRecommender {
  private sessions = new Map<
    string,
    { vector: number[]; items: string[]; categories: Record<string, number>; brands: Record<string, number>; events: number }
  >();
  private clicks = new Map<string, number>();
  private views = new Map<string, number>();
  private trend = new Map<string, number>();
  private covisit = new Map<string, Map<string, number>>();
  private experiment = structuredClone(SEED_EXPERIMENT) as Record<string, { impressions: number; clicks: number; purchases: number; revenue: number }>;
  private impressions = new Map<string, { variant: string; items: string[]; clicked: boolean }>();
  private latencies: number[] = [];
  private retrievalLatencies: number[] = [];
  private rankingLatencies: number[] = [];
  private served = 0;
  private events = 0;
  private startedAt = Date.now();

  constructor() {
    // A long-tail popularity prior so the control variant is a real baseline.
    CATALOG.forEach((item, index) => {
      const base = 40 / (index + 6) + (item.rating - 4) * 4;
      this.trend.set(item.id, Math.max(0.6, base));
      this.views.set(item.id, Math.round(base * 12));
      this.clicks.set(item.id, Math.round(base * 3));
    });
  }

  catalog(): CatalogItem[] {
    return CATALOG.map(({ latent, ...rest }) => ({ ...rest, trend_score: Number((this.trend.get(rest.id) ?? 0).toFixed(2)) }));
  }

  categories(): string[] {
    return Array.from(new Set(CATALOG.map((item) => item.category))).sort();
  }

  trending(limit: number): CatalogItem[] {
    return Array.from(this.trend.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit)
      .map(([id, score]) => {
        const item = CATALOG.find((candidate) => candidate.id === id)!;
        const { latent, ...rest } = item;
        void latent;
        return { ...rest, trend_score: Number(score.toFixed(2)) };
      });
  }

  variantFor(userId: string, forced?: string): string {
    if (forced === CONTROL || forced === TREATMENT) return forced;
    return hashBucket(`ranker-v1-vs-popularity:${userId}`) < 0.5 ? CONTROL : TREATMENT;
  }

  private session(sessionId: string) {
    let state = this.sessions.get(sessionId);
    if (!state) {
      state = { vector: new Array(DIM).fill(0), items: [], categories: {}, brands: {}, events: 0 };
      this.sessions.set(sessionId, state);
    }
    return state;
  }

  private popularity(itemId: string): number {
    const total = Array.from(this.clicks.values()).reduce((a, b) => a + b, 0) || 1;
    return (this.clicks.get(itemId) ?? 0) / total;
  }

  private ctr(itemId: string): number {
    const views = this.views.get(itemId) ?? 0;
    return views ? (this.clicks.get(itemId) ?? 0) / views : 0;
  }

  recommend(params: { user_id: string; session_id?: string; limit?: number; category?: string | null; variant?: string | null; exclude?: string[] }): RecommendResponse {
    const started = performance.now();
    const sessionId = params.session_id || params.user_id;
    const limit = params.limit ?? 12;
    const variant = this.variantFor(params.user_id, params.variant ?? undefined);
    const state = this.session(sessionId);
    const cold = state.items.length === 0;
    const query = state.vector.some((v) => v !== 0) ? unit(state.vector) : new Array(DIM).fill(0);
    const excluded = new Set([...(params.exclude ?? []), ...state.items.slice(-3)]);

    const retrievalStart = performance.now();
    let pool = CATALOG.filter((item) => (params.category ? item.category === params.category : true));
    const covisitScores = new Map<string, number>();
    state.items.slice(-6).forEach((itemId, index) => {
      const neighbours = this.covisit.get(itemId);
      if (!neighbours) return;
      neighbours.forEach((weight, neighbour) => {
        covisitScores.set(neighbour, (covisitScores.get(neighbour) ?? 0) + weight * 0.85 ** index);
      });
    });
    if (variant === CONTROL) {
      pool = [...pool].sort((a, b) => (this.trend.get(b.id) ?? 0) - (this.trend.get(a.id) ?? 0)).slice(0, 120);
    }
    const retrievalMs = performance.now() - retrievalStart;

    const rankingStart = performance.now();
    const scored = pool.map((item) => {
      if (variant === CONTROL) {
        return { item, score: (this.trend.get(item.id) ?? 0) / 60, features: {} as Record<string, number> };
      }
      const features = {
        mf_score: cold ? 0 : dot(query, unit(item.latent)),
        session_affinity: cold ? 0 : dot(query, unit(item.latent)),
        covisit: Math.log1p(covisitScores.get(item.id) ?? 0),
        popularity: this.popularity(item.id) * 100,
        item_ctr: this.ctr(item.id),
        rating: item.rating,
        category_affinity: (state.categories[item.category] ?? 0) / (Object.values(state.categories).reduce((a, b) => a + b, 0) || 1),
        brand_affinity: (state.brands[item.brand] ?? 0) / (Object.values(state.brands).reduce((a, b) => a + b, 0) || 1),
      };
      const score =
        1.9 * features.mf_score +
        0.55 * features.covisit +
        0.09 * features.popularity +
        0.5 * features.item_ctr +
        0.12 * (features.rating - 4) +
        0.85 * features.category_affinity +
        0.4 * features.brand_affinity +
        (cold ? 0.05 * Math.random() : 0);
      return { item, score, features };
    });
    const rankingMs = performance.now() - rankingStart;

    // MMR diversification with a per-category cap, same shape as the service.
    const ordered = scored.filter((row) => !excluded.has(row.item.id)).sort((a, b) => b.score - a.score);
    const chosen: typeof ordered = [];
    const categoryCount: Record<string, number> = {};
    while (chosen.length < limit && ordered.length) {
      let bestIndex = 0;
      let bestValue = -Infinity;
      ordered.slice(0, limit * 4).forEach((row, index) => {
        if ((categoryCount[row.item.category] ?? 0) >= 6) return;
        const penalty = chosen.length
          ? Math.max(...chosen.map((picked) => dot(unit(picked.item.latent), unit(row.item.latent))))
          : 0;
        const value = 0.82 * row.score - 0.18 * penalty;
        if (value > bestValue) {
          bestValue = value;
          bestIndex = index;
        }
      });
      const [picked] = ordered.splice(bestIndex, 1);
      if (!picked) break;
      categoryCount[picked.item.category] = (categoryCount[picked.item.category] ?? 0) + 1;
      chosen.push(picked);
    }

    const items: ScoredItem[] = chosen.map((row, index) => ({
      rank: index + 1,
      item_id: row.item.id,
      title: row.item.title,
      brand: row.item.brand,
      category: row.item.category,
      price: row.item.price,
      rating: row.item.rating,
      image_url: row.item.image_url,
      image_credit: row.item.image_credit,
      alt_text: row.item.alt_text || row.item.title,
      score: Number(row.score.toFixed(5)),
      reason:
        variant === CONTROL
          ? "trending now"
          : (covisitScores.get(row.item.id) ?? 0) > 0
            ? "often viewed together"
            : cold
              ? "trending now"
              : "similar to what you liked",
      features: Object.fromEntries(Object.entries(row.features).map(([k, v]) => [k, Number(v.toFixed(5))])),
    }));

    const totalMs = performance.now() - started;
    const requestId = `req_${Math.random().toString(16).slice(2, 18)}`;
    this.impressions.set(requestId, { variant, items: items.map((item) => item.item_id), clicked: false });
    this.experiment[variant].impressions += 1;
    this.served += 1;
    this.latencies.push(totalMs);
    this.retrievalLatencies.push(retrievalMs);
    this.rankingLatencies.push(rankingMs);
    if (this.latencies.length > 500) this.latencies = this.latencies.slice(-400);
    items.forEach((item) => this.views.set(item.item_id, (this.views.get(item.item_id) ?? 0) + 1));

    return {
      request_id: requestId,
      user_id: params.user_id,
      session_id: sessionId,
      variant,
      surface: "home",
      cold_start: cold,
      items,
      stage_counts:
        variant === CONTROL
          ? { trending: pool.length, merged: pool.length }
          : { ann: Math.min(120, pool.length), covisit: covisitScores.size, trending: 40, merged: pool.length },
      timings_ms: {
        retrieval: Number(retrievalMs.toFixed(3)),
        ranking: Number(rankingMs.toFixed(3)),
        total: Number(totalMs.toFixed(3)),
      },
      session_signal: {
        events: state.events,
        items: state.items.length,
        top_categories: Object.fromEntries(Object.entries(state.categories).sort((a, b) => b[1] - a[1]).slice(0, 3)),
        vector_norm: Number(norm(state.vector).toFixed(4)),
      },
    };
  }

  ingest(params: { user_id: string; session_id?: string; item_id: string; event: string; request_id?: string | null }) {
    const started = performance.now();
    const sessionId = params.session_id || params.user_id;
    const state = this.session(sessionId);
    const item = CATALOG.find((candidate) => candidate.id === params.item_id);
    const weight = { view: 1, click: 3, add_to_cart: 5, purchase: 8 }[params.event] ?? 1;
    this.events += 1;
    state.events += 1;

    if (params.event === "view") {
      this.views.set(params.item_id, (this.views.get(params.item_id) ?? 0) + 1);
    } else if (item) {
      this.clicks.set(params.item_id, (this.clicks.get(params.item_id) ?? 0) + 1);
      this.trend.set(params.item_id, (this.trend.get(params.item_id) ?? 0) + weight);
      const vector = unit(item.latent);
      state.vector = state.vector.map((v, i) => 0.86 * v + 0.14 * weight * vector[i]);
      state.categories[item.category] = (state.categories[item.category] ?? 0) + weight;
      state.brands[item.brand] = (state.brands[item.brand] ?? 0) + weight;
      state.items.slice(-6).forEach((previous) => {
        if (previous === params.item_id) return;
        const bucket = this.covisit.get(previous) ?? new Map<string, number>();
        bucket.set(params.item_id, (bucket.get(params.item_id) ?? 0) + 1);
        this.covisit.set(previous, bucket);
      });
      if (!state.items.includes(params.item_id)) state.items.push(params.item_id);
      if (state.items.length > 24) state.items = state.items.slice(-24);
    }

    if (params.request_id) {
      const impression = this.impressions.get(params.request_id);
      if (impression && !impression.clicked && params.event !== "view") {
        impression.clicked = true;
        this.experiment[impression.variant].clicks += 1;
        if (params.event === "purchase") {
          this.experiment[impression.variant].purchases += 1;
          this.experiment[impression.variant].revenue += item?.price ?? 0;
        }
      }
    }

    return {
      accepted: true,
      event: params.event,
      session_items: state.items.length,
      feature_update_us: Number(((performance.now() - started) * 1000).toFixed(1)),
      profile_strength: Number(norm(state.vector).toFixed(4)),
    };
  }

  private percentile(values: number[], q: number): number | null {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return Number(sorted[Math.min(sorted.length - 1, Math.round((sorted.length - 1) * q))].toFixed(3));
  }

  experimentReport(): ExperimentReport {
    const control = this.experiment[CONTROL];
    const treatment = this.experiment[TREATMENT];
    const controlCtr = control.impressions ? control.clicks / control.impressions : null;
    const treatmentCtr = treatment.impressions ? treatment.clicks / treatment.impressions : null;
    let uplift: number | null = null;
    let z: number | null = null;
    if (controlCtr && treatmentCtr) {
      uplift = treatmentCtr / controlCtr - 1;
      const pooled = (control.clicks + treatment.clicks) / (control.impressions + treatment.impressions);
      const se = Math.sqrt(pooled * (1 - pooled) * (1 / control.impressions + 1 / treatment.impressions));
      z = se ? (treatmentCtr - controlCtr) / se : null;
    }
    return {
      experiment: "ranker-v1-vs-popularity",
      control: { variant: CONTROL, ...control, ctr: controlCtr ? Number(controlCtr.toFixed(4)) : null },
      treatment: { variant: TREATMENT, ...treatment, ctr: treatmentCtr ? Number(treatmentCtr.toFixed(4)) : null },
      ctr_uplift: uplift === null ? null : Number(uplift.toFixed(4)),
      z_score: z === null ? null : Number(z.toFixed(2)),
      significant_95: z !== null && Math.abs(z) >= 1.96,
    };
  }

  stats(): Stats {
    const metrics = modelMetrics as Record<string, unknown>;
    return {
      generated_at: new Date().toISOString(),
      platform: { version: "1.0.0", env: "demo", database: "in-browser", uptime_s: (Date.now() - this.startedAt) / 1000, model_loaded: true },
      index: {
        items: CATALOG.length,
        factors: Number((metrics.factors as number) ?? 32),
        ivf_clusters: Number((metrics.ivf_clusters as number) ?? 9),
        probe_clusters: 6,
        known_users: Number((metrics.users as number) ?? 0),
      },
      traffic: {
        served_total: this.served,
        events_total: this.events,
        qps_60s: Number((this.served / Math.max(1, (Date.now() - this.startedAt) / 1000)).toFixed(2)),
        active_sessions: this.sessions.size,
        cold_start_rate: 0,
      },
      latency_ms: {
        p50: this.percentile(this.latencies, 0.5),
        p95: this.percentile(this.latencies, 0.95),
        p99: this.percentile(this.latencies, 0.99),
        retrieval_p95: this.percentile(this.retrievalLatencies, 0.95),
        ranking_p95: this.percentile(this.rankingLatencies, 0.95),
        samples: this.latencies.length,
      },
      features: {
        session_vectors: this.sessions.size,
        user_vectors: this.sessions.size,
        covisit_pairs: Array.from(this.covisit.values()).reduce((total, bucket) => total + bucket.size, 0),
        feature_lag_s: 0,
        pending_writes: 0,
      },
      experiment: this.experimentReport(),
      offline_metrics: (metrics.offline as Stats["offline_metrics"]) ?? {},
      model: {
        trained_at: metrics.trained_at as string,
        ranker: metrics.ranker as Stats["model"]["ranker"],
        ndcg_lift_over_popularity: metrics.ndcg_lift_over_popularity as number,
        als_seconds: metrics.als_seconds as number,
      },
    };
  }

  simulate(users: number, steps: number): SimulateResult {
    const started = performance.now();
    let clicks = 0;
    let purchases = 0;
    let served = 0;
    for (let user = 0; user < users; user += 1) {
      const userId = `sim_${Math.random().toString(16).slice(2, 8)}`;
      const sessionId = `sim_ses_${user}_${Date.now()}`;
      const seeds = [CATALOG[Math.floor(Math.random() * CATALOG.length)], CATALOG[Math.floor(Math.random() * CATALOG.length)]];
      const taste = unit(seeds[0].latent.map((v, i) => v + seeds[1].latent[i]));
      for (let step = 0; step < steps; step += 1) {
        const response = this.recommend({ user_id: userId, session_id: sessionId, limit: 12 });
        served += 1;
        for (const item of response.items) {
          const raw = CATALOG.find((candidate) => candidate.id === item.item_id)!;
          const affinity = dot(taste, unit(raw.latent));
          const probability = 1 / (1 + Math.exp(-(6 * affinity - 2.4 - 0.1 * item.rank)));
          if (Math.random() < 0.95 * probability + 0.05 * 0.06) {
            this.ingest({ user_id: userId, session_id: sessionId, item_id: item.item_id, event: "click", request_id: response.request_id });
            clicks += 1;
            if (Math.random() < 0.12) {
              this.ingest({ user_id: userId, session_id: sessionId, item_id: item.item_id, event: "purchase", request_id: response.request_id });
              purchases += 1;
            }
            break;
          }
        }
      }
    }
    const seconds = (performance.now() - started) / 1000;
    return {
      users,
      steps,
      requests_served: served,
      clicks,
      purchases,
      seconds: Number(seconds.toFixed(2)),
      requests_per_second: Number((served / Math.max(0.001, seconds)).toFixed(1)),
      experiment: this.experimentReport(),
    };
  }
}

let instance: MockRecommender | null = null;

export function mockRecommender(): MockRecommender {
  if (!instance) instance = new MockRecommender();
  return instance;
}
