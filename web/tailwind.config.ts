import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: {
          50: "#FBFAF8",
          100: "#F4F1EC",
          200: "#E8E3DA",
          300: "#D6CFC2",
        },
        graphite: {
          900: "#14120F",
          800: "#1F1C18",
          700: "#302B25",
          600: "#4A433A",
          500: "#6B6255",
        },
        copper: {
          DEFAULT: "#B4552B",
          soft: "#D97B4A",
          deep: "#8A3D1C",
        },
        moss: "#4A6B4F",
        wine: "#8C2F39",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "ui-serif", "Georgia", "serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      letterSpacing: { label: "0.14em" },
      boxShadow: {
        card: "0 1px 2px rgba(20,18,15,0.04), 0 12px 32px -20px rgba(20,18,15,0.24)",
        lift: "0 2px 6px rgba(20,18,15,0.06), 0 24px 48px -24px rgba(20,18,15,0.3)",
      },
      keyframes: {
        rise: { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        flash: { "0%": { backgroundColor: "rgba(180,85,43,0.16)" }, "100%": { backgroundColor: "transparent" } },
        pulseDot: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
      },
      animation: {
        rise: "rise 0.35s ease-out both",
        flash: "flash 1.2s ease-out",
        "pulse-dot": "pulseDot 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
