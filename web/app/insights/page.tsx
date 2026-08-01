"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { Activity, BarChart3, CheckCircle2, FlaskConical, Loader2, Play, Timer, TrendingUp } from "lucide-react";
import { USE_MOCKS, getStats, runSimulation } from "@/lib/api";
import type { SimulateResult, Stats } from "@/lib/types";
import { ms, num, pct, signedPct } from "@/lib/format";
import { Badge, BarMeter, KeyValue, MetricTile, Panel, SectionHeading, Sparkline } from "@/components/ui";

export default function InsightsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [latencySeries, setLatencySeries] = useState<number[]>([]);
  const [ctrSeries, setCtrSeries] = useState<number[]>([]);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<SimulateResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getStats();
      setStats(next);
      if (next.latency_ms.p95) setLatencySeries((prev) => [...prev, next.latency_ms.p95!].slice(-40));
      const uplift = next.experiment.ctr_uplift;
      if (uplift !== null && uplift !== undefined) setCtrSeries((prev) => [...prev, uplift * 100].slice(-40));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "service unreachable");
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const simulate = async (users: number, steps: number) => {
    setRunning(true);
    try {
      const result = await runSimulation(users, steps);
      setLastRun(result);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "simulation failed");
    } finally {
      setRunning(false);
    }
  };

  const experiment = stats?.experiment;
  const offline = stats?.offline_metrics ?? {};
  const ndcg = offline.ndcg_at_10 ?? {};
  const recall = offline.recall_at_10 ?? {};
  const maxNdcg = Math.max(0.001, ...Object.values(ndcg));

  return (
    <div className="mx-auto max-w-[1200px] px-5 py-10">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <SectionHeading
          eyebrow="Insights"
          title="Serving quality, latency budget and the live experiment"
          description="Offline metrics come from a chronological split the models never saw. Online metrics come from traffic through the same API the storefront uses."
        />
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={USE_MOCKS ? "copper" : "good"}>{USE_MOCKS ? "in-browser demo" : "live service"}</Badge>
          {stats ? <Badge>{stats.platform.database}</Badge> : null}
          {stats?.model.trained_at ? <Badge>trained {stats.model.trained_at.slice(0, 10)}</Badge> : null}
        </div>
      </header>

      {error ? <p className="mt-4 font-mono text-[12px] text-wine">{error}</p> : null}

      {/* headline metrics -------------------------------------------------- */}
      <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
        <MetricTile label="p50 latency" value={ms(stats?.latency_ms.p50)} tone="good" hint={`${num(stats?.latency_ms.samples ?? null)} requests`} />
        <MetricTile label="p95 latency" value={ms(stats?.latency_ms.p95)} hint={`p99 ${ms(stats?.latency_ms.p99)}`} />
        <MetricTile label="retrieval p95" value={ms(stats?.latency_ms.retrieval_p95)} hint="stage 1" />
        <MetricTile label="ranking p95" value={ms(stats?.latency_ms.ranking_p95)} hint="stage 2" />
        <MetricTile
          label="CTR uplift"
          value={signedPct(experiment?.ctr_uplift ?? null)}
          tone={(experiment?.ctr_uplift ?? 0) > 0 ? "good" : "bad"}
          hint={experiment?.significant_95 ? `significant (z=${experiment.z_score})` : `z=${experiment?.z_score ?? "—"}`}
        />
        <MetricTile
          label="NDCG@10"
          value={num(ndcg.two_stage ?? null, 3)}
          tone="good"
          hint={`vs ${num(ndcg.popularity ?? null, 3)} popularity`}
        />
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <Panel title="p95 latency" hint="3s samples">
          <div className="px-4 pb-4 pt-3">
            <Sparkline values={latencySeries} height={62} />
            <div className="mt-2 flex justify-between font-mono text-[11px] text-graphite-500">
              <span>qps {num(stats?.traffic.qps_60s ?? null, 2)}</span>
              <span>served {num(stats?.traffic.served_total ?? null)}</span>
              <span>sessions {num(stats?.traffic.active_sessions ?? null)}</span>
            </div>
          </div>
        </Panel>
        <Panel title="CTR uplift over control" hint="percentage points of relative lift">
          <div className="px-4 pb-4 pt-3">
            <Sparkline values={ctrSeries} height={62} tone="#4A6B4F" />
            <div className="mt-2 flex justify-between font-mono text-[11px] text-graphite-500">
              <span>control {pct(experiment?.control.ctr ?? null, 2)}</span>
              <span>treatment {pct(experiment?.treatment.ctr ?? null, 2)}</span>
              <span>{experiment?.significant_95 ? "95% significant" : "collecting"}</span>
            </div>
          </div>
        </Panel>
      </div>

      {/* experiment ------------------------------------------------------- */}
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel
          title="Online experiment"
          hint={experiment?.experiment}
          action={
            experiment?.significant_95 ? (
              <Badge tone="good">
                <CheckCircle2 className="h-3 w-3" />
                significant
              </Badge>
            ) : (
              <Badge>collecting</Badge>
            )
          }
        >
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            {[experiment?.control, experiment?.treatment].map((arm, index) => (
              <div
                key={arm?.variant ?? index}
                className={clsx(
                  "rounded-lg border p-4",
                  index === 1 ? "border-copper/35 bg-copper/[0.05]" : "border-paper-200 bg-paper-100/60",
                )}
              >
                <div className="flex items-center justify-between">
                  <p className="text-[13px] font-semibold text-graphite-900">{arm?.variant ?? "—"}</p>
                  <Badge tone={index === 1 ? "copper" : "neutral"}>{index === 1 ? "treatment" : "control"}</Badge>
                </div>
                <p className="mt-3 font-mono text-[26px] tabular-nums text-graphite-900">{pct(arm?.ctr ?? null, 2)}</p>
                <p className="text-[11.5px] text-graphite-500">click-through rate</p>
                <dl className="mt-3 space-y-1 font-mono text-[11.5px] text-graphite-600">
                  <div className="flex justify-between">
                    <dt>impressions</dt>
                    <dd>{num(arm?.impressions ?? null)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt>clicks</dt>
                    <dd>{num(arm?.clicks ?? null)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt>purchases</dt>
                    <dd>{num(arm?.purchases ?? null)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt>revenue</dt>
                    <dd>${num(arm?.revenue ?? null)}</dd>
                  </div>
                </dl>
              </div>
            ))}
          </div>
          <div className="border-t border-paper-200 px-4 py-3 text-[13px] leading-relaxed text-graphite-600">
            Users are bucketed by a hash of their id, so assignment is sticky and independent of traffic order. The
            two-proportion z-test above uses impressions as the denominator and one click per impression as the numerator.
          </div>
        </Panel>

        <Panel title="Generate traffic" hint="drives the real serving path">
          <div className="space-y-3 p-4">
            <p className="text-[13px] leading-relaxed text-graphite-600">
              Simulated shoppers have a hidden taste vector and click what they actually like. Both variants get traffic,
              so the CTR comparison is meaningful rather than decorative.
            </p>
            <div className="grid grid-cols-3 gap-2">
              {[
                { users: 40, steps: 4, label: "quick" },
                { users: 120, steps: 5, label: "standard" },
                { users: 250, steps: 6, label: "long" },
              ].map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  className="btn-ghost !px-2 !py-2 text-[12.5px]"
                  disabled={running}
                  onClick={() => simulate(preset.users, preset.steps)}
                >
                  {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                  {preset.label}
                </button>
              ))}
            </div>
            {lastRun ? (
              <dl className="row-divide overflow-hidden rounded-lg border border-paper-200">
                <KeyValue label="requests served" value={num(lastRun.requests_served)} />
                <KeyValue label="throughput" value={`${num(lastRun.requests_per_second, 1)} rps`} />
                <KeyValue label="clicks" value={num(lastRun.clicks)} />
                <KeyValue label="purchases" value={num(lastRun.purchases)} />
                <KeyValue label="wall clock" value={`${num(lastRun.seconds, 2)}s`} />
                <KeyValue label="uplift after run" value={signedPct(lastRun.experiment.ctr_uplift)} />
              </dl>
            ) : (
              <p className="font-mono text-[11.5px] text-graphite-500">no run yet</p>
            )}
          </div>
        </Panel>
      </div>

      {/* offline metrics -------------------------------------------------- */}
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel
          title="Offline evaluation"
          hint="chronological hold-out"
          action={
            <Badge tone="good">
              <TrendingUp className="h-3 w-3" />
              {signedPct(stats?.model.ndcg_lift_over_popularity ?? null)} vs popularity
            </Badge>
          }
        >
          <div className="space-y-4 p-4">
            <div className="space-y-3">
              {(["popularity", "mf", "two_stage"] as const).map((strategy) => (
                <div key={strategy} className="space-y-1.5">
                  <div className="flex items-baseline justify-between">
                    <span className="text-[13px] text-graphite-700">
                      {strategy === "mf" ? "embeddings only" : strategy === "two_stage" ? "two-stage (retrieval + ranker)" : "popularity baseline"}
                    </span>
                    <span className="font-mono text-[12.5px] tabular-nums text-graphite-900">
                      NDCG {num(ndcg[strategy] ?? null, 3)} · recall {num(recall[strategy] ?? null, 3)}
                    </span>
                  </div>
                  <BarMeter
                    value={ndcg[strategy] ?? 0}
                    max={maxNdcg}
                    tone={strategy === "two_stage" ? "bg-copper" : strategy === "mf" ? "bg-graphite-500" : "bg-moss"}
                  />
                </div>
              ))}
            </div>
            <dl className="row-divide overflow-hidden rounded-lg border border-paper-200">
              <KeyValue label="users evaluated" value={num(offline.users_evaluated ?? null)} />
              <KeyValue label="catalogue coverage @10" value={pct(offline.catalog_coverage_at_10 ?? null, 1)} />
              <KeyValue label="ranker AUC (train)" value={num(stats?.model.ranker?.train_auc ?? null, 4)} />
              <KeyValue label="ranker AP (train)" value={num(stats?.model.ranker?.train_ap ?? null, 4)} />
              <KeyValue label="ALS fit time" value={`${num(stats?.model.als_seconds ?? null, 2)}s`} />
            </dl>
            <p className="text-[12.5px] leading-relaxed text-graphite-500">
              Embeddings alone underperform popularity on short sessions — combining both in a learned ranker is what
              produces the lift. That is the argument for two stages rather than one.
            </p>
          </div>
        </Panel>

        <div className="space-y-3">
          <Panel title="Retrieval index" hint="stage 1">
            <dl className="row-divide">
              <KeyValue label="items indexed" value={num(stats?.index.items ?? null)} />
              <KeyValue label="embedding dimensions" value={num(stats?.index.factors ?? null)} />
              <KeyValue label="IVF cells" value={num(stats?.index.ivf_clusters ?? null)} />
              <KeyValue label="cells probed per query" value={num(stats?.index.probe_clusters ?? null)} />
              <KeyValue label="known users" value={num(stats?.index.known_users ?? null)} />
            </dl>
          </Panel>

          <Panel title="Streaming feature store" hint="in-memory, batched to storage">
            <dl className="row-divide">
              <KeyValue label="session vectors" value={num(stats?.features.session_vectors ?? null)} />
              <KeyValue label="user vectors" value={num(stats?.features.user_vectors ?? null)} />
              <KeyValue label="co-visitation pairs" value={num(stats?.features.covisit_pairs ?? null)} />
              <KeyValue label="feature lag" value={stats?.features.feature_lag_s !== null && stats?.features.feature_lag_s !== undefined ? `${num(stats.features.feature_lag_s, 2)}s` : "—"} />
              <KeyValue label="pending durable writes" value={num(stats?.features.pending_writes ?? null)} />
              <KeyValue label="events ingested" value={num(stats?.traffic.events_total ?? null)} />
            </dl>
          </Panel>

          <Panel title="Reading these numbers">
            <ul className="space-y-2.5 px-4 py-3 text-[13px] leading-relaxed text-graphite-600">
              <li className="flex gap-2">
                <Timer className="mt-0.5 h-3.5 w-3.5 shrink-0 text-graphite-500" strokeWidth={1.8} />
                Retrieval is sub-millisecond because the IVF probe touches a fraction of the catalogue; ranking dominates
                the budget and scales with candidate count.
              </li>
              <li className="flex gap-2">
                <FlaskConical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-graphite-500" strokeWidth={1.8} />
                Online CTR here comes from simulated shoppers with known preferences — the uplift is real relative to the
                baseline, but the absolute rate is not a market number.
              </li>
              <li className="flex gap-2">
                <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-graphite-500" strokeWidth={1.8} />
                Offline NDCG and recall are computed on interactions after the training cut-off, so they are not
                contaminated by the fitting data.
              </li>
              <li className="flex gap-2">
                <Activity className="mt-0.5 h-3.5 w-3.5 shrink-0 text-graphite-500" strokeWidth={1.8} />
                Feature lag is the age of the newest durable write; the online path never waits for it.
              </li>
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}
