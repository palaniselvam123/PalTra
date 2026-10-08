"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Landmark, Loader2, Wallet as WalletIcon, X } from "lucide-react";
import { inr, smaApi, type Wallet, type WalletLoan } from "@/lib/smaApi";

const POLL_MS = 10_000;
const SEEN_KEY = "wallet.loanSeen";
const QUICK = [1_000, 10_000, 1_00_000, 10_00_000];

/** ₹ in short Indian style for the chip: ₹8,450 · ₹12.3 L · ₹1.4 Cr. */
function short(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return `${sign}₹${Math.round(a).toLocaleString("en-IN")}`;
}

/**
 * The PAPER bots' practice wallet, top right on every page: free balance and
 * any loan. Click for Add money / Withdraw / Repay loan / margin. Pops up once
 * when a bot borrows because the balance was short of an entry's margin.
 */
export function WalletChip({ className }: { className?: string }) {
  const [w, setW] = useState<Wallet | null>(null);
  const [open, setOpen] = useState(false);
  const [loanPopup, setLoanPopup] = useState<WalletLoan | null>(null);

  const load = useCallback(() => {
    smaApi
      .wallet()
      .then((next) => {
        setW(next);
        const loan = next.last_loan;
        if (loan) {
          let seen = "";
          try {
            seen = localStorage.getItem(SEEN_KEY) ?? "";
          } catch {
            /* private window */
          }
          if (loan.at !== seen) setLoanPopup(loan);
        }
      })
      .catch(() => {
        /* the terminal is not answering; keep the last figures */
      });
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      load();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const dismissLoan = () => {
    if (loanPopup) {
      try {
        localStorage.setItem(SEEN_KEY, loanPopup.at);
      } catch {
        /* private window */
      }
    }
    setLoanPopup(null);
  };

  const active = w?.active ?? false;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={active ? "Practice wallet of the PAPER bots: free balance and loan" : "Load practice money for the PAPER bots"}
        className={clsx(
          "inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 text-left leading-tight ring-1 ring-inset hover:bg-white/5",
          active && (w?.loan ?? 0) > 0 ? "ring-amber-400/60" : "ring-white/15",
          className
        )}
      >
        <WalletIcon size={14} aria-hidden className="text-sky-400" />
        {active && w ? (
          <span>
            <span className="block text-[9px] uppercase tracking-[0.08em] text-slate-500">Balance</span>
            <span className="block font-mono text-xs font-semibold tabular-nums text-slate-100">
              {short(w.available)}
              {w.loan > 0 ? <span className="ml-1 text-amber-300">· loan {short(w.loan)}</span> : null}
            </span>
          </span>
        ) : (
          <span className="text-xs font-semibold text-sky-300">Add money</span>
        )}
      </button>
      {open ? <WalletDialog wallet={w} onChange={setW} onClose={() => setOpen(false)} /> : null}
      {loanPopup && !open ? <LoanPopup loan={loanPopup} onClose={dismissLoan} onOpenWallet={() => { dismissLoan(); setOpen(true); }} /> : null}
    </>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    box.current?.querySelector<HTMLElement>("input, button")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // Portaled: a header with a backdrop blur would otherwise clip a fixed overlay to its own box.
  return createPortal(
    <div className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} role="dialog" aria-modal="true" aria-label={title} className="w-full max-w-md rounded-xl border border-border bg-surface p-4 text-sm shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-slate-100">{title}</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-white/5">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body
  );
}

function LoanPopup({ loan, onClose, onOpenWallet }: { loan: WalletLoan; onClose: () => void; onOpenWallet: () => void }) {
  return (
    <Modal title="Loan taken for an order" onClose={onClose}>
      <div role="alert" className="space-y-2">
        <p className="text-slate-200">
          <b>{loan.symbol}</b> ×{loan.qty} at {inr(loan.price)} needed <b>{inr(loan.need)}</b> margin, but only{" "}
          <b>{inr(Math.max(0, loan.available))}</b> was free. The order was placed with a loan of{" "}
          <b className="text-amber-300">{inr(loan.borrowed)}</b>.
        </p>
        <p className="rounded-md border border-amber-400/50 bg-amber-400/[0.1] px-2 py-1.5 font-semibold text-amber-200">
          Loan to pay back: {inr(loan.loan)}
        </p>
        <p className="text-xs text-slate-400">Practice money (PAPER). Add money or repay the loan from the wallet.</p>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" onClick={onClose} className="min-h-9 rounded-md px-3 text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5">
          OK
        </button>
        <button type="button" onClick={onOpenWallet} className="min-h-9 rounded-md bg-sky-600 px-3 font-semibold text-white hover:bg-sky-500">
          Open wallet
        </button>
      </div>
    </Modal>
  );
}

function WalletDialog({ wallet, onChange, onClose }: { wallet: Wallet | null; onChange: (w: Wallet) => void; onClose: () => void }) {
  const [amount, setAmount] = useState("");
  const [margin, setMargin] = useState(String(wallet?.margin_pct ?? 20));
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const value = Number(amount);
  const valid = Number.isFinite(value) && value >= 1 && value <= 1_000_000_000;

  const run = async (label: string, call: () => Promise<Wallet>, done: string) => {
    setBusy(label);
    setMsg(null);
    try {
      const next = await call();
      onChange(next);
      setMsg({ ok: true, text: done });
      setAmount("");
    } catch (e: unknown) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "Failed" });
    } finally {
      setBusy(null);
    }
  };

  const w = wallet;
  return (
    <Modal title="Practice wallet (PAPER bots)" onClose={onClose}>
      {w?.active ? (
        <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1">
          <dt className="text-slate-400">Free balance</dt>
          <dd className="text-right font-mono font-semibold text-slate-100">{inr(w.available)}</dd>
          <dt className="text-slate-400">Margin in open trades</dt>
          <dd className="text-right font-mono text-slate-200">{inr(w.blocked)}</dd>
          <dt className="text-slate-400">P&amp;L of closed trades</dt>
          <dd className={clsx("text-right font-mono", w.realized >= 0 ? "text-emerald-300" : "text-rose-300")}>
            {w.realized >= 0 ? "+" : ""}
            {inr(w.realized)}
          </dd>
          <dt className="text-slate-400">Money loaded (+ loans)</dt>
          <dd className="text-right font-mono text-slate-200">{inr(w.funds)}</dd>
          <dt className={clsx(w.loan > 0 ? "font-semibold text-amber-300" : "text-slate-400")}>Loan to pay back</dt>
          <dd className={clsx("text-right font-mono", w.loan > 0 ? "font-semibold text-amber-300" : "text-slate-200")}>{inr(w.loan)}</dd>
        </dl>
      ) : (
        <p className="mb-3 text-slate-300">
          Load practice money for Bots 1–4 in PAPER. Each entry blocks {wallet?.margin_pct ?? 20}% of its value as intraday
          margin (100 shares at ₹10 block ₹200); it comes back with the P&amp;L when the trade closes. If the balance is short,
          the bot borrows the rest and tells you the loan.
        </p>
      )}

      <label className="block text-xs text-slate-400">
        Amount ₹ (1 to 100,00,00,000)
        <input
          type="number"
          inputMode="decimal"
          min={1}
          max={1_000_000_000}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="e.g. 100000"
          className="mt-1 block min-h-10 w-full rounded-md border border-white/15 bg-black/30 px-2 font-mono text-base text-slate-100"
        />
      </label>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {QUICK.map((q) => (
          <button key={q} type="button" onClick={() => setAmount(String(q))} className="min-h-8 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5">
            {short(q)}
          </button>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!valid || busy != null}
          onClick={() => run("add", () => smaApi.walletAdd(value), `Added ${inr(value)}.`)}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-emerald-600 px-3 font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy === "add" ? <Loader2 size={14} className="animate-spin" /> : null} Add money
        </button>
        {w?.active ? (
          <>
            <button
              type="button"
              disabled={!valid || busy != null}
              onClick={() => run("withdraw", () => smaApi.walletWithdraw(value), `Withdrew ${inr(value)}.`)}
              className="min-h-9 rounded-md px-3 text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
            >
              Withdraw
            </button>
            <button
              type="button"
              disabled={w.loan <= 0 || busy != null}
              onClick={() => run("repay", () => smaApi.walletRepay(valid ? value : undefined), "Loan repaid from the free balance.")}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-amber-200 ring-1 ring-inset ring-amber-400/50 hover:bg-amber-400/10 disabled:opacity-50"
              title="Pays the amount typed, or as much of the loan as the free balance covers"
            >
              <Landmark size={14} aria-hidden /> Repay loan
            </button>
          </>
        ) : null}
      </div>

      <div className="mt-4 flex items-end gap-2 border-t border-white/10 pt-3">
        <label className="text-xs text-slate-400">
          Intraday margin % (20 = 5×, 100 = no leverage)
          <input
            type="number"
            min={1}
            max={100}
            value={margin}
            onChange={(e) => setMargin(e.target.value)}
            className="mt-1 block min-h-9 w-28 rounded-md border border-white/15 bg-black/30 px-2 font-mono text-slate-100"
          />
        </label>
        <button
          type="button"
          disabled={busy != null || !(Number(margin) >= 1 && Number(margin) <= 100)}
          onClick={() => run("margin", () => smaApi.walletMargin(Number(margin)), `Margin set to ${Number(margin)}%.`)}
          className="min-h-9 rounded-md px-3 text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5 disabled:opacity-50"
        >
          Save margin
        </button>
        {w?.active ? (
          <button
            type="button"
            disabled={busy != null}
            onClick={() => {
              if (window.confirm("Close the practice wallet? The balance and loan go to zero and PAPER bots trade without a wallet."))
                void run("reset", () => smaApi.walletReset(), "Wallet closed.");
            }}
            className="ml-auto min-h-9 rounded-md px-3 text-xs text-rose-300 hover:bg-rose-500/10"
          >
            Close wallet
          </button>
        ) : null}
      </div>
      {msg ? (
        <p role="status" className={clsx("mt-3 text-xs", msg.ok ? "text-emerald-300" : "text-rose-300")}>
          {msg.text}
        </p>
      ) : null}
      <p className="mt-3 text-[11px] text-slate-500">
        Practice money only. LIVE bots use your real Groww balance; replays and the Research desk keep their own.
      </p>
    </Modal>
  );
}
