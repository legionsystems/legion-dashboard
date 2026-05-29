/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: [
          "JetBrains Mono",
          "IBM Plex Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Helvetica Neue",
          "sans-serif",
        ],
        display: [
          "Inter",
          "Archivo Black",
          "Inter Black",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
      },
      colors: {
        canvas: "#0A0A0A",
        surface: "#121212",
        panel: "#161616",
        raised: "#1C1C1C",
        edge: "#262626",
        "edge-strong": "#3A3A3A",
        "fg-primary": "#EAEAEA",
        "fg-secondary": "#8A8A8A",
        "fg-muted": "#5A5A5A",
        alert: "#E61919",
        phosphor: "#4AF626",
        // Status hues - dark-tuned
        "st-draft": "#6B7280",
        "st-debated": "#A855F7",
        "st-approved": "#3B82F6",
        "st-active": "#F59E0B",
        "st-review": "#F97316",
        "st-certified": "#14B8A6",
        "st-pr": "#6366F1",
        "st-ready": "#06B6D4",
        "st-merged": "#22C55E",
        "st-blocked": "#EF4444",
        "st-completed": "#10B981",
      },
      letterSpacing: {
        "telemetry": "0.08em",
        "tighter-display": "-0.04em",
      },
      borderRadius: {
        none: "0",
      },
      boxShadow: {
        inset: "inset 0 1px 0 0 rgba(255,255,255,0.04)",
      },
    },
  },
  plugins: [],
};
