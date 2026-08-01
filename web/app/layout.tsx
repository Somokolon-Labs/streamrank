import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono, Fraunces } from "next/font/google";
import "./globals.css";
import { Footer, Nav } from "@/components/chrome";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });
const serif = Fraunces({ subsets: ["latin"], variable: "--font-serif", display: "swap", axes: ["SOFT"] });

export const metadata: Metadata = {
  title: { default: "StreamRank — real-time recommendations", template: "%s · StreamRank" },
  description:
    "Two-stage recommender: ALS embeddings over an IVF index, co-visitation and trending candidates, a gradient-boosted ranker, and session features that update within milliseconds of a click.",
  keywords: ["recommendation system", "two-tower", "ALS", "ranking", "streaming features", "A/B testing", "MLOps"],
  authors: [{ name: "Shahriar Ahmed Seam" }],
  openGraph: {
    title: "StreamRank — real-time recommendations",
    description: "Retrieval + ranking in under 30ms p95, with a live A/B experiment and streaming session features.",
    type: "website",
  },
  icons: { icon: [{ url: "/favicon.svg", type: "image/svg+xml" }] },
};

export const viewport: Viewport = { themeColor: "#FBFAF8", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${serif.variable}`}>
      <body className="min-h-screen">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-graphite-900 focus:px-3 focus:py-2 focus:text-paper-50"
        >
          Skip to content
        </a>
        <Nav />
        <main id="main">{children}</main>
        <Footer />
      </body>
    </html>
  );
}
