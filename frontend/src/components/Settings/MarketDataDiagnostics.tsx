"use client";

import { useState } from "react";
import clsx from "clsx";
import { Stethoscope, Loader2, CheckCircle2, XCircle, Lock } from "lucide-react";
import { api } from "@/lib/api";

const VERDICT_COPY: Record<string, { tone: "ok" | "warn" | "bad"; title: string }> = {
  READY: { tone: "ok", title: "Live market data is available" },
  NO_MARKET_DATA_ENTITLEMENT: { tone: "warn", title: "Your Groww key has no market-data permission" },
  MARKET_DATA_ERROR: { tone: "bad", title: "Market data failed for a non-permission reason" },
  SESSION_BROKEN: { tone: "bad", title: "Groww session is not working" },
  NO_SESSION: { tone: "warn", title: "Not connected to Groww yet" },
};

export function MarketDataDiagnostics() {
  const [result, setResult] = useState<any>(null);
  const [running, setRunning] = useState(false);

  const run = async () => {
    setRunning(true);
    try {
      setResult(await api.diagnoseMarketData());
    } catch (e: any) {
      setResult({ verdict: "MARKET_DATA_ERROR", detail: e.message ?? "Diagnostics failed", checks: [] });
    } finally {
      setRunning(false);
    }
  };

  const verdict = result ? VERDICT_COPY[result.verdict] ?? VERDICT_COPY.MARKET_DATA_ERROR : null;

  return (
    <div className="rounded-lg border border-border bg-surface p-5 space-y-4">
      <h2 className="text-sm font-medium text-slate-200">Live Data Diagnostics</h2>
      <p className="text-xs text-slate-500">
        Checks each Groww capability separately, so you can tell a credential problem from a missing market-data
        subscription. Account access and market data are billed and permissioned independently.
      </p>

      <button
        onClick={run}
        disabled={running}
        className="px-3 py-1.5 rounded-md bg-bot/20 text-bot text-xs font-medium hover:bg-bot/30 transition flex items-center gap-1.5"
      >
        {running ? <Loader2 size={13} className="animate-spin" /> : <Stethoscope size={13} />}
        Run Diagnostics
      </button>

      {result && verdict && (
        <div className="space-y-3">
          <div
            className={clsx(
              "text-xs rounded-md px-3 py-2 border",
              verdict.tone === "ok" && "bg-profit/10 border-profit/30 text-profit",
              verdict.tone === "warn" && "bg-amber-500/10 border-amber-500/30 text-amber-300",
              verdict.tone === "bad" && "bg-loss/10 border-loss/30 text-loss"
            )}
          >
            <div className="font-semibold mb-1">{verdict.title}</div>
            <div className="opacity-90">{result.detail}</div>
          </div>

          {result.checks.length > 0 && (
            <table className="w-full text-[11px] font-mono">
              <tbody>
                {result.checks.map((c: any) => (
                  <tr key={c.name} className="border-b border-border/50 last:border-0">
                    <td className="py-1 w-5">
                      {c.ok ? (
                        <CheckCircle2 size={13} className="text-profit" />
                      ) : c.forbidden ? (
                        <Lock size={13} className="text-amber-400" />
                      ) : (
                        <XCircle size={13} className="text-loss" />
                      )}
                    </td>
                    <td className="py-1 text-slate-300">{c.name}</td>
                    <td className="py-1 text-slate-500">{c.kind}</td>
                    <td className="py-1 text-right text-slate-500">
                      {c.ok ? "allowed" : c.forbidden ? "forbidden" : "error"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
