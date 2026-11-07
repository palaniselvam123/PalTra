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
    id: "most-active-columns",
    title: "Which column finds the trend? Every column explained",
    summary:
      "How to read the Most active card's \"Which column finds the trend?\" test: what it does (a picture of one day), what \"kept going\" means, the \"Best for …\" grid, each column of the big table (verdict, top kept going, edge vs all, weakest, top − weakest, one hour later, average move, readings) with real numbers as the example, how to read your own results, what to do next and a word list.",
    topic: "Scalp",
    file: "/guides/most-active-columns-guide.pdf",
    pages: 4,
    minutes: 7,
    added: "2026-11-07",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "Sort by one column, take the top stocks, and see if they kept moving their way to the close.",
      "Edge = top kept going minus all stocks: 54% − 36% = +18 points means the column helps (Strong ≥ +10).",
      "Top − weakest must be positive too: if the weakest do as well as the top, the column is not telling them apart.",
      "Under 30 readings is too few to judge; re-run as more scans are saved, and prove it with a paper bot.",
    ],
  },
  {
    id: "sideways-test",
    title: "Sideways test: does sideways or trending hold?",
    summary:
      "How the Scalp page's Sideways test checks, on your own stocks and past days, whether SMA crosses, SMA 9 turns, ADX, efficiency, candle overlap or the SMA gap tell you what the next hour does, how to run it, how to read a signal card, and what to change in Settings when a signal works (or what to do when none does).",
    topic: "Backtests",
    file: "/guides/sideways-test-guide.pdf",
    pages: 3,
    minutes: 6,
    added: "2026-11-05",
    open: { href: "/scalp/", label: "Open the Scalp page" },
    points: [
      "At 10:00-14:30 each past day it reads 6 chop signals on the last hour and checks if the next hour trended.",
      "A signal is useful only if its buckets are far apart (spread 20+ points = strong, under 10 = guessing).",
      "Turn a strong signal into a setting (trade cap, ADX, SMA gap), then prove it with a replay.",
      "Run it on the backtest server; it never places an order.",
    ],
  },
  {
    id: "day-high-low",
    title: "Day high/low guard: don't buy at the top",
    summary:
      "What the three boxes mean (near the high/low, wait for a pullback, wait at most), with candle charts of a dip that gives a cheaper buy, no dip so no trade, a signal far from the high, and the short version.",
    topic: "Entries",
    file: "/guides/day-high-low-guard-guide.pdf",
    pages: 3,
    minutes: 5,
    added: "2026-11-04",
    open: { href: "/settings/#sma-day-extreme", label: "Open the setting" },
    points: [
      "Signal within 0.15% of the day's high (₹1.50 on ₹1,000): don't buy yet.",
      "Wait for the price to dip 0.15% (or to the SMA 9 if nearer), then buy there.",
      "No dip within 3 candles: no trade, the move is let go.",
      "Shorts work the same upside down, near the day's low.",
    ],
  },
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
