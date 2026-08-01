import clsx from "clsx";
import type { ReactNode } from "react";

export function SectionHeading({
  eyebrow,
  title,
  description,
  className,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  className?: string;
}) {
  return (
    <div className={clsx("max-w-2xl", className)}>
      {eyebrow ? <p className="label">{eyebrow}</p> : null}
      <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-[28px]">{title}</h2>
      {description ? <p className="mt-2.5 text-[15px] leading-relaxed text-graphite-600">{description}</p> : null}
    </div>
  );
}

export function Panel({
  title,
  hint,
  action,
  children,
  className,
}: {
  title?: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx("card overflow-hidden", className)}>
      {title ? (
        <header className="flex items-center gap-3 border-b border-paper-200 px-4 py-3">
          <h3 className="text-sm font-semibold text-graphite-900">{title}</h3>
          {hint ? <span className="font-mono text-[11px] text-graphite-500">{hint}</span> : null}
          {action ? <div className="ml-auto">{action}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function MetricTile({
  label,
  value,
  unit,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const toneClass = { default: "text-graphite-900", good: "text-moss", warn: "text-copper", bad: "text-wine" }[tone];
  return (
    <div className="card px-4 py-3.5">
      <p className="label">{label}</p>
      <p className="mt-2 flex items-baseline gap-1.5">
        <span className={clsx("font-mono text-[25px] font-medium leading-none tabular-nums", toneClass)}>{value}</span>
        {unit ? <span className="font-mono text-[11px] text-graphite-500">{unit}</span> : null}
      </p>
      {hint ? <p className="mt-1.5 text-xs text-graphite-500">{hint}</p> : null}
    </div>
  );
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2.5">
      <span className="text-[13px] text-graphite-500">{label}</span>
      <span className="font-mono text-[13px] tabular-nums text-graphite-800">{value}</span>
    </div>
  );
}

export function BarMeter({ value, max, tone = "bg-copper", label }: { value: number; max: number; tone?: string; label?: string }) {
  const width = Math.max(2, Math.min(100, (value / Math.max(1e-9, max)) * 100));
  return (
    <div className="space-y-1">
      {label ? <p className="font-mono text-[11px] text-graphite-500">{label}</p> : null}
      <div className="h-1.5 overflow-hidden rounded-full bg-paper-200">
        <div className={clsx("h-full rounded-full transition-all duration-500", tone)} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

export function Sparkline({ values, height = 42, tone = "#B4552B" }: { values: number[]; height?: number; tone?: string }) {
  if (values.length < 2) {
    return <div style={{ height }} className="flex items-end font-mono text-[11px] text-graphite-500">collecting…</div>;
  }
  const width = 240;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none">
      <polygon points={`0,${height} ${points.join(" ")} ${width},${height}`} fill={tone} opacity="0.1" />
      <polyline points={points.join(" ")} fill="none" stroke={tone} strokeWidth="1.7" strokeLinejoin="round" />
    </svg>
  );
}

export function CodeBlock({ children, caption }: { children: string; caption?: string }) {
  return (
    <figure className="overflow-hidden rounded-lg border border-paper-200 bg-graphite-900">
      {caption ? (
        <figcaption className="border-b border-white/10 px-3.5 py-2 font-mono text-[11px] uppercase tracking-label text-paper-300">
          {caption}
        </figcaption>
      ) : null}
      <pre className="scroll-slim overflow-x-auto px-3.5 py-3 font-mono text-[12.5px] leading-relaxed text-paper-100">
        {children}
      </pre>
    </figure>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "good" | "copper" | "bad"; children: ReactNode }) {
  const map = {
    neutral: "border-paper-300 bg-paper-100 text-graphite-600",
    good: "border-moss/30 bg-moss/10 text-moss",
    copper: "border-copper/30 bg-copper/10 text-copper-deep",
    bad: "border-wine/30 bg-wine/10 text-wine",
  }[tone];
  return <span className={clsx("chip", map)}>{children}</span>;
}
