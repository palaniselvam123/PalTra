/** Colours and words for news sentiment, shared by the News panel, the pop-ups and Settings. */
export function toneClass(sentiment: string | null | undefined): string {
  if (sentiment === "POSITIVE") return "text-emerald-300 ring-emerald-400/40";
  if (sentiment === "NEGATIVE") return "text-rose-300 ring-rose-400/40";
  if (sentiment === "MIXED") return "text-amber-300 ring-amber-400/40";
  return "text-slate-300 ring-white/15";
}

export function toneWord(sentiment: string | null | undefined): string {
  if (sentiment === "POSITIVE") return "Positive";
  if (sentiment === "NEGATIVE") return "Negative";
  if (sentiment === "MIXED") return "Mixed";
  return "Neutral";
}

/** Action codes from news_impact.action that mean "do something now". */
export function urgentAction(code: string | null | undefined): boolean {
  return code === "EXIT" || code === "COVER";
}

/** Colour for a suggested direction: green for long-side codes, red for short-side ones. */
/** Colour of a timing call: now (strong), later (softer), don't (muted / amber). */
export function timingClass(code: string | null | undefined): string {
  if (code === "BUY_NOW") return "text-emerald-300";
  if (code === "SHORT_NOW") return "text-rose-300";
  if (code === "BUY_LATER") return "text-emerald-200/80";
  if (code === "SHORT_LATER") return "text-rose-200/80";
  if (code === "DONT_BUY" || code === "DONT_SHORT") return "text-amber-300";
  return "text-slate-400";
}

export function directionClass(code: string | null | undefined): string {
  if (code === "BUY" || code === "PREFER_LONG" || code === "COVER") return "text-emerald-300";
  if (code === "SHORT" || code === "PREFER_SHORT" || code === "EXIT") return "text-rose-300";
  if (code === "TIGHTEN" || code === "AVOID") return "text-amber-300";
  return "text-slate-300";
}
