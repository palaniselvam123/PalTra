/**
 * The Learn page's lessons (components/Learn/LearnPage.tsx). Each lesson is a guide file kept in
 * frontend/public/guides/, served with the rest of the site. Add a lesson by dropping its file there and
 * adding an entry here; the newest first. Display only.
 */
export type Lesson = {
  id: string;
  title: string;
  summary: string;
  /** Which part of the app it explains (the topic filter). */
  topic: "SMA strategy" | "Exits" | "Entries" | "Backtests" | "Scalp" | "Getting started";
  /** Site path of the guide file. */
  file: string;
  pages: number;
  minutes: number;
  added: string;
  /** Where in the app the setting lives, and the button text for it. */
  open?: { href: string; label: string };
  points: string[];
};

export const LESSONS: Lesson[] = [
  {
    id: "sma9-slope",
    title: "SMA 9 slope: Turn exit and V-turn",
    summary:
      "What slope means in % and rupees, the difference between Turn exit, Steep run, Turn back and Turn back within, with candle charts of a V that fires, a slow top and a turn that comes too late.",
    topic: "Exits",
    file: "/guides/sma9-slope-guide.pdf",
    pages: 6,
    minutes: 8,
    added: "2026-11-04",
    open: { href: "/settings/#sma-slope-editor", label: "Open the slope settings" },
    points: [
      "Slope = how much the SMA 9 line moves per candle, in % of the price (0.02% on ₹1,000 = ₹0.20).",
      "V-turn = steep run + turn back + within N candles, all together.",
      "Turn exit = one level on its own: any fall that steep closes the trade.",
      "How to get fewer whipsaws, with a worked example of a real trade.",
    ],
  },
];
