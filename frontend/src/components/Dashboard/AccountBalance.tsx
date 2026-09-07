"use client";

import { useState } from "react";
import clsx from "clsx";
import { Wallet, TrendingUp, Layers } from "lucide-react";
import type { AccountSummary } from "@/lib/api";
import { api } from "@/lib/api";
import { Meter } from "@/components/ui/viz";

const money = (n: number) =>
  `₹${Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (n: number) => `${n < 0 ? "−" : "+"}${money(n)}`;

export function AccountBalance({
  account,
  onCapitalChanged,
}: {
  account: AccountSummary | null;
  onCapitalChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const credit = async (amount: number) => {
    setBusy(true);
    setNote(null);
    try {
      await api.loadVirtualMoney(amount);
      setNote(`+₹${amount.toLocaleString("en-IN")} loaded`);
      onCapitalChanged?.();
    } catch (e: any) {
      setNote(e.message ?? "Load failed");
    } finally {
      setBusy(false);
    }
  };
  if (!account) {
    return <div className="rounded-card border border-border bg-surface p-4 text-xs text-slate-500">Loading balance…</div>;
  }

  const grew = account.balance >= account.starting_capital;
  // Leverage above ~5x is beyond what intraday MIS normally permits.
  const overLeveraged = account.exposure_ratio > 5;

  return (
    <div className="space-y-3 rounded-card border border-border bg-surface p-4">
      <div className="flex items-center gap-2">
        <span
          className="grid h-7 w-7 place-items-center rounded-lg text-white"
          style={{ background: "linear-gradient(135deg, rgb(var(--c-bot)), rgb(var(--c-indigo)))" }}
        >
          <Wallet size={14} />
        </span>
        <span className="text-section text-slate-200">Virtual Account</span>
        <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-profit/10 text-profit border border-profit/30">
          NOT REAL MONEY
        </span>
      </div>

      <div className="relative overflow-hidden rounded-card border border-border p-3">
        <div
          className="pointer-events-none absolute inset-0 opacity-60"
          style={{
            background: grew
              ? "radial-gradient(110% 90% at 100% 0%, rgb(var(--c-profit)/0.14), transparent 62%)"
              : "radial-gradient(110% 90% at 100% 0%, rgb(var(--c-loss)/0.14), transparent 62%)",
          }}
        />
        <div className="relative">
          <div className="text-caption uppercase text-slate-500">Balance (cash)</div>
          <div className={clsx("font-mono text-metric tabular-nums", grew ? "text-profit" : "text-loss")}>
            {money(account.balance)}
          </div>
          <div className="mt-0.5 text-caption tracking-normal text-slate-500">
            from {money(account.starting_capital)} ·{" "}
            <span className={grew ? "text-profit" : "text-loss"}>
              {account.return_pct >= 0 ? "+" : ""}
              {account.return_pct}%
            </span>
          </div>
          {/* Equity against starting capital, so the bar has a fixed, meaningful
              reference rather than floating with the data. */}
          <div className="mt-2">
            <Meter
              value={Math.min(account.equity, account.starting_capital * 2)}
              max={account.starting_capital * 2 || 1}
              tone={grew ? "rgb(var(--c-profit))" : "rgb(var(--c-loss))"}
            />
          </div>
          <div className="relative mt-3 flex flex-wrap items-center gap-1.5">
            {[10_000, 50_000, 100_000].map((n) => (
              <button
                key={n}
                type="button"
                disabled={busy}
                onClick={() => credit(n)}
                className="rounded-md border border-border bg-surface2 px-2 py-1 text-[10px] text-slate-300 hover:bg-white/5 disabled:opacity-50"
              >
                Load ₹{(n / 1000).toFixed(0)}k
              </button>
            ))}
            {note && <span className="text-[10px] text-profit">{note}</span>}
          </div>
        </div>
      </div>

      {/* Buying power. `balance` deliberately does not move when a position
          opens (MIS exposure is notional, not a cash deduction), so this is
          the line that answers "how much can I still deploy?" and it does
          change the moment a trade fills. */}
      {account.buying_power_total !== undefined && (
        <div className="rounded-md border border-border bg-surface2 px-3 py-2">
          <div className="flex items-baseline justify-between">
            <span className="text-caption text-slate-400">
              Available to deploy{account.leverage ? ` · ${account.leverage}× MIS` : ""}
            </span>
            <span className="font-mono text-body font-semibold tabular-nums text-slate-100">
              {money(account.buying_power_available ?? 0)}
            </span>
          </div>
          <div className="mt-1.5">
            <Meter
              value={account.buying_power_used ?? 0}
              max={account.buying_power_total || 1}
              tone="rgb(var(--c-indigo))"
            />
          </div>
          <div className="mt-1 flex justify-between text-caption text-slate-400">
            <span>used {money(account.buying_power_used ?? 0)}</span>
            <span>of {money(account.buying_power_total)}</span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border pt-3 text-[11px]">
        <Row label="Realised today" value={signed(account.realised_today)} tone={account.realised_today} />
        <Row label="Realised all-time" value={signed(account.realised_all_time)} tone={account.realised_all_time} />
        <Row label="Unrealised (open)" value={signed(account.unrealised)} tone={account.unrealised} />
        <Row
          label="Equity"
          value={money(account.equity)}
          tone={account.equity - account.starting_capital}
          hint="balance + unrealised"
        />
      </div>

      {account.open_positions > 0 && (
        <div className="border-t border-border pt-3 text-[11px] space-y-1">
          <div className="flex items-center gap-1.5 text-slate-400">
            <Layers size={12} />
            {account.open_positions} open · exposure {money(account.open_exposure)}
          </div>
          <div className={clsx("font-mono", overLeveraged ? "text-loss" : "text-slate-500")}>
            {account.exposure_ratio}× your balance
            {overLeveraged && " — beyond typical MIS leverage"}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, tone, hint }: { label: string; value: string; tone: number; hint?: string }) {
  return (
    <div>
      <div className="text-slate-500">{label}</div>
      <div className={clsx("font-mono", tone > 0 ? "text-profit" : tone < 0 ? "text-loss" : "text-slate-300")}>
        {value}
      </div>
      {hint && <div className="text-[10px] text-slate-600">{hint}</div>}
    </div>
  );
}
