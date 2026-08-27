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
        border: "rgb(var(--c-border) / <alpha-value>)",
        profit: "rgb(var(--c-profit) / <alpha-value>)",
        loss: "rgb(var(--c-loss) / <alpha-value>)",
        bot: "rgb(var(--c-bot) / <alpha-value>)",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
