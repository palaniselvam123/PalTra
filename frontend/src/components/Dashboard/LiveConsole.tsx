"use client";

import clsx from "clsx";
import type { LogEntry } from "@/hooks/useTradingState";

export function LiveConsole({ logs }: { logs: LogEntry[] }) {
  return (
    <div className="rounded-lg border border-border bg-surface flex flex-col h-[360px]">
      <div className="px-4 py-3 border-b border-border text-sm font-medium text-slate-200">Live Strategy Terminal</div>
      <div className="flex-1 overflow-y-auto px-4 py-2 font-mono text-xs space-y-1">
        {logs.length === 0 && <div className="text-slate-500">Waiting for events…</div>}
        {logs.map((log) => (
          <div key={log.id} className="flex gap-2">
            <span className="text-slate-600 shrink-0">{log.at}</span>
            <span
              className={clsx(
                "shrink-0",
                log.level === "ERROR" && "text-loss",
                log.level === "WARN" && "text-amber-400",
                log.level === "INFO" && "text-bot"
              )}
            >
              [{log.level}]
            </span>
            <span className="text-slate-300 break-all">{log.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
