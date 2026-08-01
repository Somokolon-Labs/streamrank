/**
 * StreamRank API client. Set NEXT_PUBLIC_USE_MOCKS=false plus
 * NEXT_PUBLIC_API_URL to run the storefront against the live service.
 */

import { mockRecommender } from "./mock";
import type { CatalogItem, ExperimentReport, RecommendResponse, SimulateResult, Stats } from "./types";

export const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS !== "false";
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8200").replace(/\/$/, "");
const API_KEY = process.env.NEXT_PUBLIC_DEMO_API_KEY ?? "demo-key-streamrank";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "content-type": "application/json", "X-API-Key": API_KEY, ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function getCatalog(category?: string): Promise<{ items: CatalogItem[]; categories: string[] }> {
  if (USE_MOCKS) {
    const world = mockRecommender();
    const items = world.catalog();
    return { items: category ? items.filter((item) => item.category === category) : items, categories: world.categories() };
  }
  const data = await request<{ items: CatalogItem[]; categories: string[] }>(
    `/v1/catalog?limit=200${category ? `&category=${encodeURIComponent(category)}` : ""}`,
  );
  return { items: data.items, categories: data.categories };
}

export async function getTrending(limit = 8): Promise<CatalogItem[]> {
  if (USE_MOCKS) return mockRecommender().trending(limit);
  const data = await request<{ items: CatalogItem[] }>(`/v1/trending?limit=${limit}`);
  return data.items;
}

export async function recommend(params: {
  user_id: string;
  session_id: string;
  limit?: number;
  category?: string | null;
  variant?: string | null;
  exclude?: string[];
}): Promise<RecommendResponse> {
  if (USE_MOCKS) return mockRecommender().recommend(params);
  return request<RecommendResponse>("/v1/recommend", {
    method: "POST",
    body: JSON.stringify({ ...params, surface: "home" }),
  });
}

export async function sendEvent(params: {
  user_id: string;
  session_id: string;
  item_id: string;
  event: "view" | "click" | "add_to_cart" | "purchase";
  request_id?: string | null;
  position?: number | null;
}): Promise<{ feature_update_us: number; session_items: number; profile_strength: number }> {
  if (USE_MOCKS) return mockRecommender().ingest(params);
  return request("/v1/events", { method: "POST", body: JSON.stringify(params) });
}

export async function getStats(): Promise<Stats> {
  if (USE_MOCKS) return mockRecommender().stats();
  return request<Stats>("/v1/stats");
}

export async function getExperiment(): Promise<ExperimentReport> {
  if (USE_MOCKS) return mockRecommender().experimentReport();
  return request<ExperimentReport>("/v1/experiment");
}

export async function runSimulation(users: number, steps: number): Promise<SimulateResult> {
  if (USE_MOCKS) return mockRecommender().simulate(users, steps);
  return request<SimulateResult>("/v1/simulate", { method: "POST", body: JSON.stringify({ users, steps }) });
}
