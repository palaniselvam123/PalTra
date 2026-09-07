"use client";

import { useEffect, useState } from "react";
import { api, type CourseAnalysis, type CourseExplanation, type CourseStatus } from "@/lib/api";

/**
 * Teaches the pattern our own engine detected, using the user's uploaded
 * course material.
 *
 * Two things this panel is careful about:
 *
 * 1. It shows what OUR engine found (`engine_pattern`) next to what the course
 *    material says about it. The RAG is handed our detection and never runs
 *    its own, so the two can't disagree — and showing both makes that visible
 *    rather than asking the user to trust it.
 * 2. The summary answers the four questions someone actually has mid-session;
 *    the full reasoning is collapsed. A wall of text at the moment a candle
 *    closes is worse than nothing.
 *
 * Advisory only: this places no orders, sizes nothing, and vetoes nothing.
 */

type Tone = "green" | "amber" | "neutral";

function deriveVerdict(
  a: CourseAnalysis,
  hasEnginePattern: boolean,
  hasPosition: boolean
): { label: string; tone: Tone; line: string } {
  if (hasPosition) {
    return {
      label: "MANAGE OPEN POSITION",
      tone: "neutral",
      line: "You are already in this trade. The levels below are your exit plan.",
    };
  }
  if (!hasEnginePattern) {
    return {
      label: "NO SETUP",
      tone: "neutral",
      line: "Our engine found no named pattern on the last closed bar. Waiting is the correct action.",
    };
  }
  if (!a.grounded) {
    return {
      label: "NOT IN YOUR MATERIAL",
      tone: "amber",
      line: "A pattern formed, but your uploaded course does not cover it. Treat with caution.",
    };
  }
  const missing = a.entry.conditions_not_met.length;
  if (missing > 0) {
    return {
      label: "WAIT",
      tone: "amber",
      line: `${missing} condition${missing > 1 ? "s" : ""} from your material still unmet.`,
    };
  }
  if (a.confidence === "low") {
    return {
      label: "WEAK SETUP",
      tone: "amber",
      line: "Conditions are met, but your material would call this a low-quality example.",
    };
  }
  return {
    label: "SETUP COMPLETE",
    tone: "green",
    line: "Every condition your course material lists is satisfied.",
  };
}

// Semantic tokens, not raw palette values: `profit`/`loss` are re-tuned per
// theme in globals.css, whereas emerald-300/rose-300 are calibrated for a
// near-black background and fail contrast on white.
const TONE: Record<Tone, string> = {
  green: "border-profit/30 bg-profit/10 text-profit",
  amber: "border-amber-500/30 bg-amber-500/10 text-amber-400",
  neutral: "border-border bg-white/[0.04] text-slate-300",
};

const CONF: Record<string, string> = {
  high: "text-profit",
  medium: "text-amber-400",
  low: "text-loss",
};

/** The model writes markdown into these fields; the summary renders plain text. */
function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

function toGlance(text: string, max = 130): string {
  const clean = stripMarkdown(text);
  const first = clean.split(/(?<=[.!?])\s/)[0] ?? clean;
  const candidate = first.length <= max ? first : clean;
  return candidate.length <= max ? candidate : `${candidate.slice(0, max).trimEnd()}…`;
}

export function CoursePanel({
  symbol,
  interval = "5m",
  hasPosition = false,
}: {
  symbol: string;
  interval?: string;
  hasPosition?: boolean;
}) {
  const [status, setStatus] = useState<CourseStatus | null>(null);
  const [data, setData] = useState<CourseExplanation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{
    answer: string;
    sources: { documentFilename: string; page: number | null }[];
  } | null>(null);
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    api.getCourseStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // A symbol change invalidates the explanation — it was about a different chart.
  useEffect(() => {
    setData(null);
    setError(null);
    setExpanded(false);
  }, [symbol, interval]);

  const explain = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.explainWithCourse(symbol, interval));
      setExpanded(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the course material.");
    } finally {
      setLoading(false);
    }
  };

  const ask = async () => {
    const q = question.trim();
    if (!q) return;
    setAsking(true);
    setError(null);
    try {
      setAnswer(await api.askCourse(q, "chart-panel"));
      setQuestion("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the course material.");
    } finally {
      setAsking(false);
    }
  };

  if (status && !status.configured) {
    return (
      <div className="rounded-card border border-border bg-surface p-4">
        <h3 className="text-sm font-semibold text-slate-200">Course coach</h3>
        <p className="mt-2 text-xs text-slate-400">
          Not connected. Set <code className="text-slate-300">COURSE_RAG_URL</code> and{" "}
          <code className="text-slate-300">COURSE_RAG_API_KEY</code> in the backend&apos;s{" "}
          <code className="text-slate-300">.env</code>, then restart.
        </p>
      </div>
    );
  }

  const a = data?.analysis;
  const ep = data?.engine_pattern ?? null;
  const verdict = a ? deriveVerdict(a, !!ep, hasPosition) : null;
  const blockers = a ? [...a.entry.conditions_not_met, ...a.cautions] : [];

  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">Course coach</h3>
        <div className="flex items-center gap-2">
          {status && (
            <span className="text-[10px] text-slate-500">
              {status.reachable ? `${status.documents_ready} docs` : "offline"}
            </span>
          )}
          <button
            onClick={explain}
            disabled={loading}
            className="rounded bg-white/[0.04] px-2 py-1 text-xs text-slate-200 hover:bg-white/[0.08] disabled:opacity-40"
          >
            {loading ? "Reading…" : `Explain ${symbol}`}
          </button>
        </div>
      </div>

      {error && <p className="mt-2 text-xs text-loss">{error}</p>}

      {data && a && verdict && (
        <div className="mt-3">
          <div className={`rounded-lg border px-3 py-2.5 ${TONE[verdict.tone]}`}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-bold tracking-wide">{verdict.label}</span>
              <span className="shrink-0 text-[10px] uppercase tracking-wide opacity-80">
                {data.symbol} · {interval}
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed opacity-90">{verdict.line}</p>
          </div>

          {/* Our engine's finding, shown so the two layers are visibly consistent. */}
          <div className="mt-2 rounded border border-border bg-surface2 px-2.5 py-2">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">
              Detected by this app&apos;s pattern engine
            </div>
            <div className="mt-0.5 text-xs text-slate-200">
              {ep ? (
                <>
                  <span className="font-medium">{ep.label}</span>
                  <span className="text-slate-500"> · {ep.bias.toLowerCase()} · after a {ep.trend.toLowerCase()} trend</span>
                </>
              ) : (
                <span className="text-slate-400">No named pattern — {data.engine_description}</span>
              )}
            </div>
            {ep && <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{ep.note}</p>}
          </div>

          <div className="mt-2 grid grid-cols-2 gap-2">
            <div className="rounded border border-border bg-surface2 px-2.5 py-2">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Your course calls it</div>
              <div className="mt-0.5 text-xs font-medium text-slate-200">{a.setup}</div>
            </div>
            <div className="rounded border border-border bg-surface2 px-2.5 py-2">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Confidence</div>
              <div className={`mt-0.5 text-xs font-medium uppercase ${CONF[a.confidence] ?? "text-slate-300"}`}>
                {a.confidence}
              </div>
            </div>
          </div>

          {(a.exit.stop_loss_level !== null || a.entry.trigger_level !== null) && (
            <div className="mt-2 rounded border border-border bg-surface2 px-2.5 py-2">
              <div className="mb-1.5 text-[10px] uppercase tracking-wide text-slate-500">
                {hasPosition ? "Your exit levels" : "Levels your material would use"}
              </div>
              <div className="grid grid-cols-3 gap-2 font-mono text-xs">
                <div>
                  <div className="text-[10px] text-slate-500">Trigger</div>
                  <div className="text-slate-200">{a.entry.trigger_level ?? "—"}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500">Stop</div>
                  <div className="text-loss">{a.exit.stop_loss_level ?? "—"}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500">Target</div>
                  <div className="text-profit">{a.exit.target_levels[0] ?? "—"}</div>
                </div>
              </div>
              {a.risk.risk_reward && (
                <div className="mt-1.5 border-t border-border pt-1.5 text-[11px] text-slate-400">
                  Risk/reward: <span className="text-slate-200">{a.risk.risk_reward}</span>
                </div>
              )}
            </div>
          )}

          {blockers.length > 0 && (
            <div className="mt-2 rounded border border-amber-500/25 bg-amber-500/10 px-2.5 py-2">
              <div className="text-[10px] uppercase tracking-wide text-amber-400/80">
                What&apos;s still needed
              </div>
              <ul className="mt-1 space-y-1">
                {blockers.slice(0, 3).map((c, i) => (
                  <li key={i} className="text-xs leading-relaxed text-amber-400">
                    • {toGlance(c)}
                  </li>
                ))}
              </ul>
              {blockers.length > 3 && (
                <div className="mt-1 text-[10px] text-amber-400/60">
                  +{blockers.length - 3} more in the full explanation
                </div>
              )}
            </div>
          )}

          <button
            onClick={() => setExpanded((v) => !v)}
            className="mt-2 flex w-full items-center gap-1.5 rounded px-1 py-1.5 text-xs text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
          >
            <span className={`transition-transform ${expanded ? "rotate-90" : ""}`}>▸</span>
            {expanded ? "Hide the full explanation" : "Why? Show the full explanation"}
          </button>

          {expanded && (
            <div className="space-y-3 border-t border-border pt-3">
              <p className="text-xs leading-relaxed text-slate-300">{stripMarkdown(a.market_summary)}</p>

              {a.teaching_notes.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Why these rules</div>
                  <ul className="mt-1 space-y-1.5">
                    {a.teaching_notes.map((n, i) => (
                      <li key={i} className="text-xs leading-relaxed text-slate-400">
                        • {stripMarkdown(n)}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {a.risk.invalidation && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">
                    What would prove this wrong
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-slate-400">
                    {stripMarkdown(a.risk.invalidation)}
                  </p>
                </div>
              )}

              {data.sources.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Sources</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {data.sources.map((s, i) => (
                      <span
                        key={i}
                        title={s.snippet}
                        className="rounded bg-white/[0.04] px-1.5 py-0.5 text-[10px] text-slate-400"
                      >
                        {s.documentFilename}
                        {s.page ? ` p.${s.page}` : ""}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <p className="text-[10px] leading-relaxed text-slate-600">{data.disclaimer}</p>
            </div>
          )}
        </div>
      )}

      <div className="mt-3 border-t border-border pt-3">
        <div className="flex gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            placeholder="Ask your course material…"
            className="flex-1 rounded border border-border bg-base px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-slate-600 focus:outline-none"
          />
          <button
            onClick={ask}
            disabled={asking || !question.trim()}
            className="rounded bg-white/[0.04] px-3 py-1.5 text-xs text-slate-200 hover:bg-white/[0.08] disabled:opacity-40"
          >
            {asking ? "…" : "Ask"}
          </button>
        </div>

        {answer && (
          <div className="mt-2 rounded border border-border bg-surface2 p-2.5">
            <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-300">
              {stripMarkdown(answer.answer)}
            </p>
            {answer.sources?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {answer.sources.map((s, i) => (
                  <span key={i} className="rounded bg-white/[0.04] px-1.5 py-0.5 text-[10px] text-slate-500">
                    {s.documentFilename}
                    {s.page ? ` p.${s.page}` : ""}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
