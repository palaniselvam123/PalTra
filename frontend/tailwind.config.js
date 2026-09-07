/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  // Themes are driven by a `data-theme` attribute on <html> rather than
  // Tailwind's class strategy, so a single attribute swaps the whole palette
  // and the CSS-variable overrides in globals.css can hang off the same hook.
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Channel triplets (not hex) so `<alpha-value>` keeps working and
        // existing classes like `bg-bot/20` or `border-profit/30` still apply.
        base: "rgb(var(--c-base) / <alpha-value>)",
        surface: "rgb(var(--c-surface) / <alpha-value>)",
        surface2: "rgb(var(--c-surface-2) / <alpha-value>)",
        border: "rgb(var(--c-border) / <alpha-value>)",
        profit: "rgb(var(--c-profit) / <alpha-value>)",
        loss: "rgb(var(--c-loss) / <alpha-value>)",
        bot: "rgb(var(--c-bot) / <alpha-value>)",
        accentViolet: "rgb(var(--c-violet) / <alpha-value>)",
        accentSky: "rgb(var(--c-sky) / <alpha-value>)",
        accentAmber: "rgb(var(--c-amber) / <alpha-value>)",
        accentTeal: "rgb(var(--c-teal) / <alpha-value>)",
        accentPink: "rgb(var(--c-pink) / <alpha-value>)",
        accentIndigo: "rgb(var(--c-indigo) / <alpha-value>)",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      // A real type ladder. The app previously lived entirely between 10px and
      // 14px, which left no way to make the number you actually glance at
      // (P&L, equity) louder than its own label.
      // Sized against the reference, which runs ~14px body and ~20px section
      // headings. The previous ladder topped out at 13px for a heading, which
      // is why every panel read as small print regardless of its colour.
      fontSize: {
        display: ["1.875rem", { lineHeight: "2.25rem", letterSpacing: "-0.02em", fontWeight: "600" }],
        metric: ["1.5rem", { lineHeight: "2rem", letterSpacing: "-0.015em", fontWeight: "600" }],
        title: ["1.25rem", { lineHeight: "1.75rem", letterSpacing: "-0.01em", fontWeight: "600" }],
        section: ["1rem", { lineHeight: "1.5rem", fontWeight: "600" }],
        body: ["0.875rem", { lineHeight: "1.375rem" }],
        caption: ["0.75rem", { lineHeight: "1.125rem" }],
      },
      boxShadow: {
        card: "0 1px 2px rgb(0 0 0 / 0.04), 0 1px 3px rgb(0 0 0 / 0.06)",
        pop: "0 4px 12px rgb(0 0 0 / 0.10), 0 12px 32px rgb(0 0 0 / 0.14)",
      },
      borderRadius: { card: "0.625rem" },
    },
  },
  plugins: [],
};
