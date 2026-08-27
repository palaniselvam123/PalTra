"use client";

import clsx from "clsx";
import { Wallet, TrendingUp, Layers } from "lucide-react";
import type { AccountSummary } from "@/lib/api";

const money = (n: number) =>
  `₹${Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const signed = (n: number) => `${n < 0 ? "−" : "+"}${money(n)}`;

export function AccountBalance({ account }: { account: AccountSummary | null }) {
  if (!account) {
    return <div className="rounded-lg border border-border bg-surface p-4 text-xs text-slate-500">Loading balance…</div>;
  }

  const grew = account.balance >= account.starting_capital;
  // Leverage above ~5x is beyond what intraday MIS normally permits.
  const overLeveraged = account.exposure_ratio > 5;

  return (
    <div className="rounded-lg border border-border bg-surface p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Wallet size={15} className="text-bot" />
        <span className="text-sm font-medium text-slate-200">Virtual Account</span>
        <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-profit/10 text-profit border border-profit/30">
          NOT REAL MONEY
        </span>
      </div>

      <div>
        <div className="text-[11px] text-slate-500">Balance (cash)</div>
        <div className={clsx("text-2xl font-mono font-semibold", grew ? "text-profit" : "text-loss")}>
          {money(account.balance)}
        </div>
        <div className="text-[11px] text-slate-500">
          started at {money(account.starting_capital)} ·{" "}
          <span className={grew ? "text-profit" : "text-loss"}>
            {account.return_pct >= 0 ? "+" : ""}
            {account.return_pct}%
          </span>
        </div>
      </div>

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
