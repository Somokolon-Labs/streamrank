"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { ArrowRight, Gauge, RefreshCcw, Sparkles, Star, Timer, Zap } from "lucide-react";
import { USE_MOCKS, getCatalog, recommend, sendEvent } from "@/lib/api";
import type { CatalogItem, RecommendResponse, ScoredItem } from "@/lib/types";
import { money, ms, num, titleCase } from "@/lib/format";
import { Badge, BarMeter, KeyValue, Panel, SectionHeading } from "@/components/ui";

const STORAGE_KEY = "streamrank.identity";

function newIdentity() {
  const suffix = Math.random().toString(16).slice(2, 8);
  return { userId: `usr_web_${suffix}`, sessionId: `ses_web_${suffix}_${Date.now()}` };
}

export default function StorefrontPage() {
  const [identity, setIdentity] = useState(newIdentity);
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [rail, setRail] = useState<RecommendResponse | null>(null);
  const [lastClick, setLastClick] = useState<{ item: ScoredItem; updateUs: number } | null>(null);
  const [history, setHistory] = useState<{ item: string; category: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [latencies, setLatencies] = useState<number[]>([]);

  useEffect(() => {
    const stored = typeof window !== "undefined" ? window.localStorage.getItem(STORAGE_KEY) : null;
    if (stored) {
      try {
        setIdentity(JSON.parse(stored));
        return;
      } catch {
        /* fall through to a fresh identity */
      }
    }
    setIdentity((current) => {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
      return current;
    });
  }, []);

  const refreshRail = useCallback(
    async (opts: { category?: string | null } = {}) => {
      setBusy(true);
      try {
        const response = await recommend({
          user_id: identity.userId,
          session_id: identity.sessionId,
          limit: 12,
          category: opts.category ?? null,
        });
        setRail(response);
        setLatencies((prev) => [...prev, response.timings_ms.total].slice(-40));
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "could not reach the service");
      } finally {
        setBusy(false);
      }
    },
    [identity],
  );

  useEffect(() => {
    getCatalog()
      .then((data) => {
        setCatalog(data.items);
        setCategories(data.categories);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "catalog unavailable"));
  }, []);

  useEffect(() => {
    refreshRail();
  }, [refreshRail]);

  const click = async (item: ScoredItem | CatalogItem, position?: number) => {
    const itemId = "item_id" in item ? item.item_id : item.id;
    const scored: ScoredItem =
      "item_id" in item
        ? item
        : {
            rank: (position ?? 0) + 1,
            item_id: item.id,
            title: item.title,
            brand: item.brand,
            category: item.category,
            price: item.price,
            rating: item.rating,
            image_url: item.image_url,
            image_credit: item.image_credit,
            alt_text: item.alt_text,
            score: 0,
            reason: "browsed",
            features: {},
          };
    try {
      const result = await sendEvent({
        user_id: identity.userId,
        session_id: identity.sessionId,
        item_id: itemId,
        event: "click",
        request_id: rail?.request_id ?? null,
        position: position ?? null,
      });
      setLastClick({ item: scored, updateUs: result.feature_update_us });
      setHistory((prev) => [{ item: scored.title, category: scored.category }, ...prev].slice(0, 8));
      await refreshRail({ category });
    } catch (err) {
      setError(err instanceof Error ? err.message : "event rejected");
    }
  };

  const reset = () => {
    const fresh = newIdentity();
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(fresh));
    setIdentity(fresh);
    setHistory([]);
    setLastClick(null);
  };

  const filtered = useMemo(
    () => (category ? catalog.filter((item) => item.category === category) : catalog).slice(0, 24),
    [catalog, category],
  );

  const topCategories = Object.entries(rail?.session_signal.top_categories ?? {});
  const maxCategoryWeight = Math.max(1, ...topCategories.map(([, weight]) => weight));

  return (
    <>
      {/* hero ----------------------------------------------------------- */}
      <section className="border-b border-paper-200 bg-white">
        <div className="mx-auto grid max-w-[1200px] gap-10 px-5 py-14 lg:grid-cols-[minmax(0,1fr)_360px] lg:py-16">
          <div className="animate-rise">
            <p className="label">Real-time recommendation system</p>
            <h1 className="mt-3 max-w-xl font-serif text-[42px] font-semibold leading-[1.05] tracking-tight sm:text-[52px]">
              Click one thing.
              <span className="text-copper"> Watch the model react.</span>
            </h1>
            <p className="mt-5 max-w-xl text-[16.5px] leading-relaxed text-graphite-600">
              Every click updates a session vector in memory, so the next request retrieves different candidates and
              ranks them differently — in single-digit milliseconds. Two-stage retrieval, a learned ranker, and an
              A/B experiment running against a popularity baseline.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-3">
              <button type="button" className="btn-copper" onClick={() => refreshRail({ category })} disabled={busy}>
                {busy ? "scoring…" : "Refresh recommendations"}
                <RefreshCcw className={clsx("h-4 w-4", busy && "animate-spin")} />
              </button>
              <button type="button" className="btn-ghost" onClick={reset}>
                New shopper
                <Sparkles className="h-4 w-4" />
              </button>
              <Link href="/insights" className="btn-ghost">
                Experiment insights
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
            {error ? <p className="mt-4 font-mono text-[12px] text-wine">{error}</p> : null}
          </div>

          <div className="card divide-y divide-paper-200">
            <div className="px-4 py-3">
              <p className="label">This request</p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Badge tone={rail?.variant === "two-stage" ? "copper" : "neutral"}>
                  variant · {rail?.variant ?? "…"}
                </Badge>
                {rail?.cold_start ? <Badge>cold start</Badge> : <Badge tone="good">personalised</Badge>}
                {USE_MOCKS ? <Badge>in-browser model</Badge> : null}
              </div>
            </div>
            <KeyValue label="total latency" value={ms(rail?.timings_ms.total)} />
            <KeyValue label="retrieval" value={ms(rail?.timings_ms.retrieval)} />
            <KeyValue label="ranking" value={ms(rail?.timings_ms.ranking)} />
            <KeyValue
              label="candidates"
              value={`${num(rail?.stage_counts.merged ?? null)} from ${Object.keys(rail?.stage_counts ?? {}).filter((k) => k !== "merged").length} sources`}
            />
            <KeyValue label="session events" value={num(rail?.session_signal.events ?? null)} />
            <KeyValue label="session vector" value={num(rail?.session_signal.vector_norm ?? null, 3)} />
            <div className="space-y-2 px-4 py-3">
              <p className="label">Session taste</p>
              {topCategories.length ? (
                topCategories.map(([name, weight]) => (
                  <BarMeter key={name} value={weight} max={maxCategoryWeight} label={`${titleCase(name)} · ${num(weight)}`} />
                ))
              ) : (
                <p className="text-[13px] text-graphite-500">click a product to build a taste profile</p>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* rail ------------------------------------------------------------ */}
      <section className="mx-auto max-w-[1200px] px-5 py-12">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <SectionHeading
            eyebrow="Personalised rail"
            title="Recommended for you"
            description="Ranked by the learned model when you are in the treatment bucket, or by trending popularity when you are in the control bucket. Reasons are shown per item."
          />
          <div className="flex items-center gap-2 font-mono text-[11px] text-graphite-500">
            <Timer className="h-3.5 w-3.5" />
            last {latencies.length} requests · median{" "}
            {ms(latencies.length ? [...latencies].sort((a, b) => a - b)[Math.floor(latencies.length / 2)] : null)}
          </div>
        </div>

        <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {(rail?.items ?? []).map((item) => (
            <button
              key={item.item_id}
              type="button"
              onClick={() => click(item, item.rank - 1)}
              className="card group overflow-hidden text-left transition-all hover:-translate-y-0.5 hover:shadow-lift"
            >
              <div className="relative aspect-[4/3] overflow-hidden bg-paper-100">
                <Image
                  src={item.image_url}
                  alt={item.alt_text}
                  fill
                  sizes="(max-width: 640px) 100vw, 300px"
                  className="object-cover transition-transform duration-500 group-hover:scale-[1.04]"
                />
                <span className="absolute left-2 top-2 rounded-full bg-graphite-900/85 px-2 py-0.5 font-mono text-[10px] text-paper-50">
                  #{item.rank}
                </span>
              </div>
              <div className="space-y-1.5 p-3.5">
                <p className="label">{item.brand}</p>
                <p className="text-[14px] font-medium leading-snug text-graphite-900">{item.title}</p>
                <div className="flex items-center justify-between pt-1">
                  <span className="font-mono text-[13px] text-graphite-900">{money(item.price)}</span>
                  <span className="flex items-center gap-1 font-mono text-[11px] text-graphite-500">
                    <Star className="h-3 w-3 fill-copper text-copper" />
                    {item.rating.toFixed(1)}
                  </span>
                </div>
                <p className="pt-1 font-mono text-[10.5px] uppercase tracking-label text-copper-deep">{item.reason}</p>
              </div>
            </button>
          ))}
          {!rail ? (
            Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="card h-72 animate-pulse bg-paper-100" />
            ))
          ) : null}
        </div>
      </section>

      {/* why this -------------------------------------------------------- */}
      {lastClick ? (
        <section className="mx-auto max-w-[1200px] px-5 pb-6">
          <Panel
            title="Why this item moved"
            hint={`feature update in ${num(lastClick.updateUs, 1)}µs`}
            action={<Badge tone="copper">streaming feature</Badge>}
            className="animate-flash"
          >
            <div className="grid gap-6 p-4 lg:grid-cols-[220px_minmax(0,1fr)]">
              <div className="flex gap-3">
                <div className="relative h-20 w-20 shrink-0 overflow-hidden rounded-lg bg-paper-100">
                  <Image src={lastClick.item.image_url} alt={lastClick.item.alt_text} fill sizes="80px" className="object-cover" />
                </div>
                <div>
                  <p className="text-[13.5px] font-medium text-graphite-900">{lastClick.item.title}</p>
                  <p className="mt-0.5 text-[12px] text-graphite-500">{titleCase(lastClick.item.category)}</p>
                  <p className="mt-1 font-mono text-[12px] text-graphite-700">{money(lastClick.item.price)}</p>
                </div>
              </div>
              <div>
                <p className="text-[13.5px] leading-relaxed text-graphite-600">
                  The click was written to the session vector, the item&apos;s trend counter and the co-visitation graph
                  before the next request was served. Ranking features for the item you clicked:
                </p>
                <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(lastClick.item.features).length ? (
                    Object.entries(lastClick.item.features).map(([name, value]) => (
                      <div key={name} className="card-quiet px-3 py-2">
                        <p className="label">{name.replace(/_/g, " ")}</p>
                        <p className="mt-1 font-mono text-[13px] tabular-nums text-graphite-900">{value.toFixed(4)}</p>
                      </div>
                    ))
                  ) : (
                    <p className="font-mono text-[12px] text-graphite-500">
                      control variant does not compute ranking features
                    </p>
                  )}
                </div>
              </div>
            </div>
          </Panel>
        </section>
      ) : null}

      {/* browse ---------------------------------------------------------- */}
      <section className="mx-auto max-w-[1200px] px-5 py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <SectionHeading eyebrow="Catalogue" title="Browse and teach the model" />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                setCategory(null);
                refreshRail({ category: null });
              }}
              className={clsx("chip transition-colors", !category ? "border-graphite-900 bg-graphite-900 text-paper-50" : "border-paper-300 bg-white text-graphite-600")}
            >
              all
            </button>
            {categories.map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => {
                  setCategory(name);
                  refreshRail({ category: name });
                }}
                className={clsx(
                  "chip transition-colors",
                  category === name ? "border-graphite-900 bg-graphite-900 text-paper-50" : "border-paper-300 bg-white text-graphite-600 hover:border-graphite-500",
                )}
              >
                {name}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {filtered.map((item, index) => (
            <button
              key={item.id}
              type="button"
              onClick={() => click(item, index)}
              className="card group overflow-hidden text-left transition-all hover:-translate-y-0.5 hover:shadow-lift"
            >
              <div className="relative aspect-square overflow-hidden bg-paper-100">
                <Image
                  src={item.image_url}
                  alt={item.alt_text}
                  fill
                  sizes="(max-width: 640px) 33vw, 190px"
                  className="object-cover transition-transform duration-500 group-hover:scale-[1.05]"
                />
              </div>
              <div className="space-y-1 p-2.5">
                <p className="truncate text-[12.5px] font-medium text-graphite-900">{item.title}</p>
                <p className="font-mono text-[11.5px] text-graphite-600">{money(item.price)}</p>
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* session history -------------------------------------------------- */}
      <section className="mx-auto max-w-[1200px] px-5 pb-12">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <Panel title="What the model knows about this session" hint="in-memory features">
            <div className="grid gap-4 p-4 sm:grid-cols-2">
              <div className="space-y-2">
                <p className="label">Recent clicks</p>
                {history.length ? (
                  <ol className="row-divide overflow-hidden rounded-lg border border-paper-200">
                    {history.map((entry, index) => (
                      <li key={`${entry.item}-${index}`} className="flex items-baseline gap-3 px-3 py-2">
                        <span className="font-mono text-[11px] text-graphite-500">{history.length - index}</span>
                        <span className="truncate text-[13px] text-graphite-800">{entry.item}</span>
                        <span className="ml-auto font-mono text-[11px] text-copper-deep">{entry.category}</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-[13px] text-graphite-500">no clicks yet — the rail is showing trending items</p>
                )}
              </div>
              <div className="space-y-3">
                <p className="label">Candidate sources for the last request</p>
                {Object.entries(rail?.stage_counts ?? {})
                  .filter(([key]) => key !== "merged")
                  .map(([source, count]) => (
                    <BarMeter
                      key={source}
                      value={count}
                      max={Math.max(1, ...Object.values(rail?.stage_counts ?? { a: 1 }))}
                      label={`${source} · ${count}`}
                      tone={source === "ann" ? "bg-copper" : source === "covisit" ? "bg-moss" : "bg-graphite-500"}
                    />
                  ))}
                <p className="pt-1 text-[12.5px] leading-relaxed text-graphite-500">
                  Candidates are merged, deduplicated and then reranked. Diversification caps any single category so
                  the rail stays browsable.
                </p>
              </div>
            </div>
          </Panel>

          <Panel title="How this works" hint="in three steps">
            <ol className="row-divide">
              {[
                {
                  icon: Zap,
                  title: "Streaming features",
                  body: "Your click updates a session EMA vector, item trend counters and the co-visitation graph synchronously, then persists in a background batch.",
                },
                {
                  icon: Gauge,
                  title: "Two-stage retrieval",
                  body: "An IVF probe over trained item embeddings plus co-visitation and trending produce ~200 candidates in well under a millisecond.",
                },
                {
                  icon: Sparkles,
                  title: "Learned ranking",
                  body: "A gradient-boosted ranker scores those candidates on ten features, then MMR diversification picks the final twelve.",
                },
              ].map((step) => (
                <li key={step.title} className="flex gap-3 px-4 py-3">
                  <step.icon className="mt-0.5 h-4 w-4 shrink-0 text-copper" strokeWidth={1.8} />
                  <div>
                    <p className="text-[13.5px] font-medium text-graphite-900">{step.title}</p>
                    <p className="mt-1 text-[12.5px] leading-relaxed text-graphite-600">{step.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      </section>
    </>
  );
}
