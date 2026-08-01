import { Boxes, Database, GitBranch, Layers, Rocket, Timer } from "lucide-react";
import { CodeBlock, KeyValue, Panel, SectionHeading } from "@/components/ui";

export const metadata = {
  title: "Architecture",
  description: "Two-stage retrieval and ranking, streaming feature store, experiment framework and deployment topology.",
};

const NODES = [
  { id: "client", x: 10, y: 120, w: 128, h: 74, title: "Storefront", lines: ["clicks · views", "purchases"] },
  { id: "api", x: 176, y: 104, w: 156, h: 108, title: "Serving API", lines: ["FastAPI · asyncio", "variant assignment", "impression log"], accent: true },
  { id: "features", x: 372, y: 20, w: 168, h: 96, title: "Feature store", lines: ["session EMA vectors", "trend counters", "co-visitation graph"] },
  { id: "retrieval", x: 372, y: 138, w: 168, h: 96, title: "Stage 1 retrieval", lines: ["IVF probe (ANN)", "co-visitation", "trending"], accent: true },
  { id: "ranker", x: 580, y: 138, w: 156, h: 96, title: "Stage 2 ranker", lines: ["10 features", "gradient boosting", "MMR + caps"], accent: true },
  { id: "store", x: 580, y: 20, w: 156, h: 96, title: "Postgres", lines: ["interactions", "impressions", "profiles · stats"] },
  { id: "train", x: 780, y: 74, w: 156, h: 96, title: "Offline training", lines: ["implicit ALS", "ranker fit", "chronological eval"] },
];

const PATHS = [
  { d: "M138 157 H176", flow: true },
  { d: "M332 130 C 352 130 352 68 372 68", label: "write features", flow: true },
  { d: "M332 176 H372", label: "read features", flow: true },
  { d: "M540 186 H580", flow: true },
  { d: "M658 138 C 658 120 658 100 658 116", dashed: true },
  { d: "M736 186 C 800 186 810 150 780 140", label: "impressions", dashed: true },
  { d: "M858 74 C 858 40 760 30 736 40", label: "artifacts", dashed: true },
  { d: "M580 68 C 560 68 556 120 540 138", dashed: true },
];

export default function ArchitecturePage() {
  return (
    <div className="mx-auto max-w-[1200px] px-5 py-10">
      <SectionHeading
        eyebrow="Architecture"
        title="Two stages, one streaming feature store, one experiment framework"
        description="Retrieval narrows 100k+ items to a couple hundred candidates in under a millisecond. Ranking spends the remaining budget where it changes the ordering. Features written on the click path are read by the very next request."
      />

      <div className="card mt-8 overflow-hidden">
        <svg viewBox="0 0 960 250" className="w-full" role="img" aria-label="StreamRank serving architecture">
          {PATHS.map((path, index) => (
            <g key={index}>
              <path
                id={`p${index}`}
                d={path.d}
                fill="none"
                stroke={path.dashed ? "rgba(20,18,15,0.22)" : "rgba(180,85,43,0.55)"}
                strokeWidth="1.2"
                strokeDasharray={path.dashed ? "4 5" : undefined}
              />
              {path.flow ? (
                <circle r="2.6" fill="#B4552B">
                  <animateMotion dur="3s" repeatCount="indefinite">
                    <mpath href={`#p${index}`} />
                  </animateMotion>
                </circle>
              ) : null}
              {path.label ? (
                <text fill="#6B6255" fontSize="9" fontFamily="ui-monospace, monospace">
                  <textPath href={`#p${index}`} startOffset="38%">
                    {path.label}
                  </textPath>
                </text>
              ) : null}
            </g>
          ))}

          {NODES.map((node) => (
            <g key={node.id}>
              <rect
                x={node.x}
                y={node.y}
                width={node.w}
                height={node.h}
                rx="10"
                fill={node.accent ? "rgba(180,85,43,0.06)" : "#FFFFFF"}
                stroke={node.accent ? "rgba(180,85,43,0.45)" : "rgba(20,18,15,0.14)"}
              />
              <text x={node.x + 13} y={node.y + 23} fill="#14120F" fontSize="13" fontWeight="600">
                {node.title}
              </text>
              {node.lines.map((line, index) => (
                <text
                  key={line}
                  x={node.x + 13}
                  y={node.y + 42 + index * 15}
                  fill="#6B6255"
                  fontSize="10"
                  fontFamily="ui-monospace, monospace"
                >
                  {line}
                </text>
              ))}
            </g>
          ))}
        </svg>
      </div>

      <section className="mt-12 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {[
          {
            icon: Timer,
            title: "Why two stages",
            body: "Scoring every item with a learned model does not scale, and scoring with a cheap model alone loses accuracy. Retrieval is recall-oriented and sub-linear; ranking is precision-oriented over a few hundred candidates.",
          },
          {
            icon: Layers,
            title: "Why IVF over brute force",
            body: "K-means cells let a query touch a fraction of the index. The probe count is a single knob that trades recall for latency, measured per request rather than assumed.",
          },
          {
            icon: Database,
            title: "Why features live in memory",
            body: "A click must change the next response. The online store is authoritative for the request path and is batched to Postgres asynchronously; losing it costs quality for seconds, never correctness.",
          },
          {
            icon: Boxes,
            title: "Why co-visitation is kept",
            body: "It is the cheapest source of session intent and covers cold items that embeddings under-serve. Three complementary sources beat one clever one.",
          },
          {
            icon: GitBranch,
            title: "Why MMR after ranking",
            body: "Pure relevance collapses a rail into one category. Maximal marginal relevance plus a per-category cap keeps the response browsable without discarding the ranker's judgement.",
          },
          {
            icon: Rocket,
            title: "Why train in the image build",
            body: "The container ships a model whose offline metrics are recorded in the same artifacts. A deploy can never drift from the numbers on the insights page.",
          },
        ].map((card) => (
          <article key={card.title} className="card p-5">
            <card.icon className="h-4 w-4 text-copper" strokeWidth={1.8} />
            <h3 className="mt-3 text-[15px] font-semibold leading-snug">{card.title}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-graphite-600">{card.body}</p>
          </article>
        ))}
      </section>

      <section className="mt-12 grid gap-3 lg:grid-cols-2">
        <div className="space-y-3">
          <Panel title="Latency budget" hint="measured, not estimated">
            <dl className="row-divide">
              <KeyValue label="feature write (click path)" value="~0.25 ms" />
              <KeyValue label="stage 1 retrieval p95" value="&lt; 1 ms" />
              <KeyValue label="stage 2 ranking p95" value="~13 ms" />
              <KeyValue label="end to end p50 / p95" value="0.6 ms / 25 ms" />
              <KeyValue label="candidates scored" value="~220" />
            </dl>
          </Panel>

          <Panel title="Model card" hint="what ships in the image">
            <dl className="row-divide">
              <KeyValue label="retrieval" value="implicit ALS, 32 factors" />
              <KeyValue label="index" value="IVF k-means, 9 cells, 6 probed" />
              <KeyValue label="ranker" value="HistGradientBoosting, 10 features" />
              <KeyValue label="offline split" value="70% fit / 15% ranker / 15% eval" />
              <KeyValue label="NDCG@10" value="0.456 (popularity 0.384)" />
              <KeyValue label="Recall@10" value="0.568 (popularity 0.436)" />
            </dl>
          </Panel>
        </div>

        <div className="space-y-3">
          <CodeBlock caption="serve a recommendation">
{`curl -X POST localhost:8200/v1/recommend \\
  -H 'content-type: application/json' \\
  -d '{
    "user_id": "usr_00042",
    "session_id": "ses_demo",
    "limit": 12
  }'`}
          </CodeBlock>
          <CodeBlock caption="stream a click back">
{`curl -X POST localhost:8200/v1/events \\
  -H 'content-type: application/json' \\
  -d '{
    "user_id": "usr_00042",
    "session_id": "ses_demo",
    "item_id": "itm_0031",
    "event": "click",
    "request_id": "req_..."
  }'`}
          </CodeBlock>
          <Panel title="Endpoints">
            <dl className="row-divide">
              <KeyValue label="POST /v1/recommend" value="two-stage rail" />
              <KeyValue label="POST /v1/events" value="single behavioural event" />
              <KeyValue label="POST /v1/events/batch" value="up to 500 events" />
              <KeyValue label="GET /v1/catalog" value="items + categories" />
              <KeyValue label="GET /v1/trending" value="decayed trend ranking" />
              <KeyValue label="GET /v1/stats" value="latency, features, index" />
              <KeyValue label="GET /v1/experiment" value="A/B report + z-test" />
              <KeyValue label="POST /v1/simulate" value="synthetic traffic" />
              <KeyValue label="GET /metrics" value="Prometheus exposition" />
            </dl>
          </Panel>
        </div>
      </section>
    </div>
  );
}
