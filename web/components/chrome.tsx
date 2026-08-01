"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { ArrowUpRight, Sparkles } from "lucide-react";
import { API_URL, USE_MOCKS } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Storefront" },
  { href: "/insights", label: "Insights" },
  { href: "/architecture", label: "Architecture" },
];

export function Mark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect x="2.5" y="2.5" width="27" height="27" rx="8" fill="none" stroke="#B4552B" strokeWidth="1.6" />
      <path d="M8 21.5 13 12l4.4 6.4L21 8.5l3 6" fill="none" stroke="#14120F" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="24" cy="14.5" r="2" fill="#B4552B" />
    </svg>
  );
}

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-40 border-b border-paper-200 bg-paper-50/90 backdrop-blur-xl">
      <div className="mx-auto flex h-15 max-w-[1200px] items-center gap-6 px-5 py-3">
        <Link href="/" className="flex items-center gap-2.5">
          <Mark />
          <span className="flex items-baseline gap-2">
            <span className="text-[15px] font-semibold tracking-tight text-graphite-900">StreamRank</span>
            <span className="hidden font-mono text-[10px] uppercase tracking-label text-graphite-500 sm:inline">
              real-time recommendations
            </span>
          </span>
        </Link>

        <nav className="ml-3 hidden items-center gap-1 md:flex">
          {LINKS.map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={clsx(
                  "rounded-lg px-3 py-1.5 text-sm transition-colors",
                  active ? "bg-graphite-900 text-paper-50" : "text-graphite-600 hover:text-graphite-900",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <span
            className={clsx(
              "chip",
              USE_MOCKS ? "border-copper/30 bg-copper/10 text-copper-deep" : "border-moss/30 bg-moss/10 text-moss",
            )}
          >
            <Sparkles className="h-3 w-3" />
            {USE_MOCKS ? "demo mode" : "live service"}
          </span>
          <a
            href={USE_MOCKS ? "https://github.com/shahriarahmedseam/streamrank" : `${API_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            className="hidden items-center gap-1 font-mono text-[11px] uppercase tracking-label text-graphite-500 hover:text-graphite-900 sm:flex"
          >
            {USE_MOCKS ? "source" : "api docs"}
            <ArrowUpRight className="h-3 w-3" />
          </a>
        </div>
      </div>

      <nav className="flex gap-1 overflow-x-auto border-t border-paper-200 px-4 py-2 md:hidden">
        {LINKS.map((link) => {
          const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={clsx(
                "whitespace-nowrap rounded-lg px-3 py-1.5 text-sm",
                active ? "bg-graphite-900 text-paper-50" : "text-graphite-600",
              )}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-20 border-t border-paper-200 bg-white">
      <div className="mx-auto grid max-w-[1200px] gap-8 px-5 py-12 sm:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-3">
          <div className="flex items-center gap-2.5">
            <Mark className="h-6 w-6" />
            <span className="text-sm font-semibold text-graphite-900">StreamRank</span>
          </div>
          <p className="max-w-xs text-sm leading-relaxed text-graphite-500">
            Two-stage retrieval and ranking with streaming session features, an online A/B experiment and latency
            budgets measured per stage.
          </p>
        </div>
        <div className="space-y-2">
          <p className="label">Pages</p>
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} className="block text-sm text-graphite-600 hover:text-graphite-900">
              {link.label}
            </Link>
          ))}
        </div>
        <div className="space-y-2">
          <p className="label">Stack</p>
          <p className="text-sm text-graphite-600">FastAPI · NumPy · scikit-learn</p>
          <p className="text-sm text-graphite-600">Postgres · Redis · Prometheus</p>
          <p className="text-sm text-graphite-600">Next.js · Docker · Kubernetes</p>
        </div>
        <div className="space-y-2">
          <p className="label">Credits</p>
          <p className="text-sm text-graphite-500">
            Built by <span className="text-graphite-800">Shahriar Ahmed Seam</span> — Somokolon Labs.
          </p>
          <p className="text-xs leading-relaxed text-graphite-500">
            Product photography from{" "}
            <a href="https://www.pexels.com" target="_blank" rel="noreferrer" className="underline">
              Pexels
            </a>
            ; catalogue titles, prices and behaviour are synthetic.
          </p>
        </div>
      </div>
      <div className="border-t border-paper-200">
        <div className="mx-auto flex max-w-[1200px] flex-col gap-2 px-5 py-4 font-mono text-[11px] uppercase tracking-label text-graphite-500 sm:flex-row sm:justify-between">
          <span>StreamRank v1.0.0 — MIT licensed</span>
          <span>p95 &lt; 30ms · NDCG@10 0.456 · +20.7% CTR</span>
        </div>
      </div>
    </footer>
  );
}
