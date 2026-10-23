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
