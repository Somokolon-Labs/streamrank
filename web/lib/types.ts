export interface CatalogItem {
  id: string;
  title: string;
  brand: string;
  category: string;
  price: number;
  rating: number;
  tags?: string[];
  image_url: string;
  image_credit: string;
  alt_text: string;
  trend_score?: number;
}

export interface ScoredItem {
  rank: number;
  item_id: string;
  title: string;
  brand: string;
  category: string;
  price: number;
  rating: number;
  image_url: string;
  image_credit: string;
  alt_text: string;
  score: number;
  reason: string;
  features: Record<string, number>;
}

export interface RecommendResponse {
  request_id: string;
  user_id: string;
  session_id: string;
  variant: string;
  surface: string;
  cold_start: boolean;
  items: ScoredItem[];
  stage_counts: Record<string, number>;
  timings_ms: { retrieval: number; ranking: number; total: number };
  session_signal: {
    events: number;
    items: number;
    top_categories: Record<string, number>;
    vector_norm: number;
  };
}

export interface VariantStats {
  variant: string;
  impressions: number;
  clicks: number;
  purchases: number;
  revenue: number;
  ctr: number | null;
}

export interface ExperimentReport {
  experiment: string;
  control: VariantStats;
  treatment: VariantStats;
  ctr_uplift: number | null;
  z_score: number | null;
  significant_95: boolean;
}

export interface Stats {
  generated_at: string;
  platform: { version: string; env: string; database: string; uptime_s: number; model_loaded: boolean };
  index: { items: number; factors: number; ivf_clusters: number; probe_clusters: number; known_users: number };
  traffic: {
    served_total: number;
    events_total: number;
    qps_60s: number;
    active_sessions: number;
    cold_start_rate: number;
  };
  latency_ms: {
    p50: number | null;
    p95: number | null;
    p99: number | null;
    retrieval_p95: number | null;
    ranking_p95: number | null;
    samples: number;
  };
  features: {
    session_vectors: number;
    user_vectors: number;
    covisit_pairs: number;
    feature_lag_s: number | null;
    pending_writes: number;
  };
  experiment: ExperimentReport;
  offline_metrics: {
    users_evaluated?: number;
    ndcg_at_10?: Record<string, number>;
    recall_at_10?: Record<string, number>;
    catalog_coverage_at_10?: number;
  };
  model: {
    trained_at?: string;
    ranker?: { model?: string; train_auc?: number; train_ap?: number };
    ndcg_lift_over_popularity?: number;
    als_seconds?: number;
  };
}

export interface SimulateResult {
  users: number;
  steps: number;
  requests_served: number;
  clicks: number;
  purchases: number;
  seconds: number;
  requests_per_second: number | null;
  experiment: ExperimentReport;
}
