"use client";

import clsx from "clsx";
import { Terminal } from "lucide-react";
import type { LogEntry } from "@/hooks/useTradingState";
import { Card, EmptyState, StatusDot } from "@/components/ui";

const LEVEL_STYLE: Record<string, string> = {
  ERROR: "text-loss",
  WARN: "text-amber-400",
  INFO: "text-bot",
};

export function LiveConsole({ logs }: { logs: LogEntry[] }) {
  return (
    <Card padded={false} className="flex h-[360px] flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-section text-slate-200">Strategy terminal</h2>
          {logs.length > 0 && (
            <span className="text-caption tabular-nums text-slate-500">{logs.length}</span>
          )}
        </div>
        <span className="flex items-center gap-1.5">
          <StatusDot tone="info" pulse />
          <span className="text-caption text-slate-500">live</span>
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {logs.length === 0 ? (
          <EmptyState
            className="h-full"
            icon={<Terminal size={22} />}
            title="No events yet"
            hint="Every decision the bot makes — range locks, signals, entries, exits and rejections — appears here with its reason."
          />
        ) : (
          <div className="space-y-0.5 px-3 py-2">
            {logs.map((log) => (
              <div
                key={log.id}
                className="flex gap-2 rounded px-1 py-0.5 font-mono text-[11px] leading-relaxed hover:bg-white/[0.03]"
              >
                <span className="shrink-0 tabular-nums text-slate-600">{log.at}</span>
                <span className={clsx("w-11 shrink-0", LEVEL_STYLE[log.level])}>{log.level}</span>
                <span className="break-all text-slate-300">{log.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
