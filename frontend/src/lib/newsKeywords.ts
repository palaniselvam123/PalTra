/**
 * "Which words move the price?" (the News page's "Did it move?" tab): every measured headline whose title holds a
 * keyword, and how far the price went after it. Arithmetic on the rows the check already returned, so changing the
 * keywords needs no new download. Display only: nothing is traded.
 */
import type { NewsCheckRow } from "@/lib/api";

/** The usual Indian corporate-news words, one entry per idea ("a|b" = either word). */
export const DEFAULT_KEYWORDS = [
  "order|contract|bags|wins",
  "results|quarterly|Q1|Q2|Q3|Q4",
  "profit|net profit",
  "loss",
  "revenue|sales",
  "guidance|outlook",
  "dividend",
  "buyback",
  "bonus",
  "split",
  "acquisition|acquire|merger|amalgamation",
  "stake|block deal|bulk deal",
  "QIP|fund raise|fundraise|rights issue|preferential",
  "upgrade|target price|buy rating",
  "downgrade|sell rating",
  "SEBI|penalty|show cause|probe|raid",
  "fraud|default|insolvency|NCLT",
  "resign|resigns|steps down|CEO|CFO",
  "USFDA|FDA|approval",
  "plant|fire|shutdown",
  "tax|GST|demand notice",
  "launch|launches",
  "expansion|capex",
  "pledge",
  "rally|surge|jumps|soars",
  "falls|slumps|tumbles|plunges",
];

export type KeywordStat = {
  keyword: string;
  headlines: number;
  measured: number;
  stocks: number;
  upClosePct: number | null;
  avg15: number | null;
  avg30: number | null;
  avg60: number | null;
  avgClose: number | null;
  avgAbsClose: number | null;
  avgBestUp: number | null;
  avgWorstDown: number | null;
  withPct: number | null;
  positive: number;
  negative: number;
};

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Split the typed box into keywords: commas or new lines between them, "|" inside one for "either". */
export function parseKeywords(text: string): string[] {
  return Array.from(new Set(text.split(/[,\n]+/).map((k) => k.trim()).filter(Boolean)));
}

/** A matcher for one keyword: each "|" part as a whole word (or phrase), case-insensitive. */
export function keywordMatcher(keyword: string): RegExp | null {
  const parts = keyword.split("|").map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return null;
  return new RegExp(`(^|[^A-Za-z0-9])(${parts.map(escape).join("|")})(?=$|[^A-Za-z0-9])`, "i");
}

function avg(values: (number | null | undefined)[]): number | null {
  const v = values.filter((x): x is number => x != null && Number.isFinite(x));
  return v.length ? Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 1000) / 1000 : null;
}

const JUDGED = new Set(["WITH", "AGAINST", "FADED", "NO_EFFECT"]);

/** One stat per keyword, biggest average move at the close first. */
export function keywordStats(rows: NewsCheckRow[], keywords: string[]): KeywordStat[] {
  const out: KeywordStat[] = [];
  for (const keyword of keywords) {
    const re = keywordMatcher(keyword);
    if (!re) continue;
    const hit = rows.filter((r) => re.test(r.title || ""));
    const measured = hit.filter((r) => r.move_close != null);
    const judged = hit.filter((r) => JUDGED.has(r.verdict));
    out.push({
      keyword,
      headlines: hit.length,
      measured: measured.length,
      stocks: new Set(hit.map((r) => r.symbol)).size,
      upClosePct: measured.length ? Math.round((measured.filter((r) => (r.move_close ?? 0) > 0).length / measured.length) * 1000) / 10 : null,
      avg15: avg(measured.map((r) => r.move_15m)),
      avg30: avg(measured.map((r) => r.move_30m)),
      avg60: avg(measured.map((r) => r.move_60m)),
      avgClose: avg(measured.map((r) => r.move_close)),
      avgAbsClose: avg(measured.map((r) => (r.move_close == null ? null : Math.abs(r.move_close)))),
      avgBestUp: avg(measured.map((r) => r.best_up)),
      avgWorstDown: avg(measured.map((r) => r.worst_down)),
      withPct: judged.length ? Math.round((judged.filter((r) => r.verdict === "WITH").length / judged.length) * 1000) / 10 : null,
      positive: hit.filter((r) => r.sentiment === "POSITIVE").length,
      negative: hit.filter((r) => r.sentiment === "NEGATIVE").length,
    });
  }
  return out.sort((a, b) => (b.measured ? 1 : 0) - (a.measured ? 1 : 0) || (b.avgAbsClose ?? -1) - (a.avgAbsClose ?? -1));
}
