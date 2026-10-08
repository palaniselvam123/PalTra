"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Landmark, Loader2, Wallet as WalletIcon, X } from "lucide-react";
import { inr, smaApi, type Wallet, type WalletEntry, type WalletLoan } from "@/lib/smaApi";

const POLL_MS = 10_000;
const SEEN_KEY = "wallet.loanSeen";
const QUICK = [1_000, 10_000, 1_00_000, 10_00_000, 1_00_00_000];
const MAX = 1_000_000_000;

/**
 * Reads a typed amount the way people write it: "2100000", "21,00,000",
 * "₹21,00,000", "21 L", "21 lakh", "2.5 Cr". NaN when it is not a number.
 */
export function parseAmount(text: string): number {
  const t = text.replace(/[₹,\s]/g, "").toLowerCase();
  if (!t) return NaN;
  const m = /^(\d+(?:\.\d+)?)(l|lakh|lakhs|lac|cr|crore|crores|k)?$/.exec(t);
  if (!m) return NaN;
  const unit = m[2] ?? "";
  const mult = unit.startsWith("l") ? 1e5 : unit.startsWith("c") ? 1e7 : unit === "k" ? 1e3 : 1;
  return Math.round(Number(m[1]) * mult * 100) / 100;
}

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

function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  // The wallet polls every few seconds and re-renders the dialog with a new
  // onClose; keep it in a ref so focus is set once, not pulled out of the
  // amount box on every poll.
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", onKey);
    (box.current?.querySelector<HTMLElement>("input") ?? box.current?.querySelector<HTMLElement>("button"))?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // Portaled: a header with a backdrop blur would otherwise clip a fixed overlay to its own box.
  return createPortal(
    <div className="terminal-dark fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={clsx(
          "max-h-[90vh] w-full overflow-y-auto rounded-xl border border-border bg-surface p-4 text-sm shadow-xl",
          wide ? "max-w-4xl" : "max-w-md"
        )}
      >
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
          {loan.bot ? <>Bot {loan.bot}: </> : null}
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
  const value = parseAmount(amount);
  const valid = Number.isFinite(value) && value >= 1 && value <= MAX;
  const plus = (q: number) => {
    const now = parseAmount(amount);
    setAmount(String(Math.min(MAX, (Number.isFinite(now) ? now : 0) + q)));
  };

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
  const [tab, setTab] = useState<"wallet" | "loans" | "statement">("wallet");
  return (
    <Modal title="Practice wallet (PAPER bots)" onClose={onClose} wide={tab !== "wallet"}>
      <div role="tablist" aria-label="Wallet" className="mb-3 flex gap-1 border-b border-white/10">
        {(
          [
            ["wallet", "Wallet"],
            ["loans", `Loans${w?.open_loans ? ` (${w.open_loans} open)` : ""}`],
            ["statement", "Statement"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={clsx(
              "-mb-px min-h-9 border-b-2 px-3 text-xs font-semibold",
              tab === id ? "border-sky-400 text-sky-300" : "border-transparent text-slate-400 hover:text-slate-200"
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "loans" ? <LoanRecords /> : tab === "statement" ? <Statement /> : (
      <>
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
        Amount ₹ (₹1 to ₹100 crore) — type 2100000, 21,00,000 or 21 L
        <input
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && valid && busy == null) void run("add", () => smaApi.walletAdd(value), `Added ${inr(value)}.`);
          }}
          placeholder="e.g. 21,00,000"
          className="mt-1 block min-h-10 w-full rounded-md border border-white/15 bg-black/30 px-2 font-mono text-base text-slate-100"
        />
      </label>
      <p className={clsx("mt-1 min-h-4 text-xs", amount && !valid ? "text-rose-300" : "text-slate-400")}>
        {!amount ? "" : valid ? `= ${inr(value)} (${short(value)})` : value > MAX ? "Up to ₹100 crore at a time." : "Not an amount."}
      </p>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {QUICK.map((q) => (
          <button key={q} type="button" onClick={() => plus(q)} title={`Adds ${inr(q)} to the amount`} className="min-h-8 rounded-md px-2 text-xs text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5">
            +{short(q)}
          </button>
        ))}
        {amount ? (
          <button type="button" onClick={() => setAmount("")} className="min-h-8 rounded-md px-2 text-xs text-slate-400 hover:bg-white/5">
            Clear
          </button>
        ) : null}
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
      </>
      )}
      <p className="mt-3 text-[11px] text-slate-500">
        Practice money only. LIVE bots use your real Groww balance; replays and the Research desk keep their own.
      </p>
    </Modal>
  );
}

const KIND_LABEL: Record<WalletEntry["kind"], string> = {
  ADD: "Money added",
  WITHDRAW: "Withdrawn",
  LOAN: "Loan taken",
  REPAY: "Loan repaid",
  RESET: "Wallet closed",
  MARGIN: "Margin changed",
};

function when(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  return `${d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} ${at.slice(11, 16)}`;
}

function useRecords(load: () => Promise<WalletEntry[]>) {
  const [rows, setRows] = useState<WalletEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    load()
      .then((r) => live && setRows(r))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Could not load the records"));
    return () => {
      live = false;
    };
  }, [load]);
  return { rows, error };
}

/** Every loan the wallet took: what it paid for, borrowed, repaid and still due. */
function LoanRecords() {
  const { rows, error } = useRecords(smaApi.walletLoans);
  if (error) return <p className="text-xs text-rose-300">{error}</p>;
  if (!rows) return <p className="text-xs text-slate-400">Loading…</p>;
  if (!rows.length) return <p className="text-slate-400">No loans yet. A loan is taken only when the free balance is short of an entry&apos;s margin.</p>;
  const due = rows.reduce((sum, r) => sum + (r.due ?? 0), 0);
  return (
    <div className="overflow-x-auto">
      <p className="mb-2 text-xs text-slate-400">
        {rows.length} loan{rows.length === 1 ? "" : "s"} · still due <b className="text-amber-300">{inr(due)}</b>. Repayments clear the
        oldest loan first. No interest.
      </p>
      <table className="w-full min-w-[640px] whitespace-nowrap text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-slate-400">
          <tr>
            <th className="px-2 py-1.5">#</th>
            <th className="px-2 py-1.5">Taken</th>
            <th className="px-2 py-1.5">For</th>
            <th className="px-2 py-1.5 text-right">Margin needed</th>
            <th className="px-2 py-1.5 text-right">Borrowed</th>
            <th className="px-2 py-1.5 text-right">Repaid</th>
            <th className="px-2 py-1.5 text-right">Due</th>
            <th className="px-2 py-1.5">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5 font-mono">
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="px-2 py-1.5 text-slate-400">L{r.id}</td>
              <td className="px-2 py-1.5 font-sans text-slate-300">{when(r.at)}</td>
              <td className="px-2 py-1.5 font-sans text-slate-200">
                Bot {r.bot ?? 1} · <b>{r.symbol}</b> ×{r.qty} @ {inr(r.price ?? 0)}
              </td>
              <td className="px-2 py-1.5 text-right text-slate-300">{inr(r.need ?? 0)}</td>
              <td className="px-2 py-1.5 text-right text-slate-100">{inr(r.amount)}</td>
              <td className="px-2 py-1.5 text-right text-emerald-300">{inr(r.repaid ?? 0)}</td>
              <td className={clsx("px-2 py-1.5 text-right", (r.due ?? 0) > 0 ? "font-semibold text-amber-300" : "text-slate-400")}>
                {inr(r.due ?? 0)}
              </td>
              <td className="px-2 py-1.5 font-sans">
                <span
                  className={clsx(
                    "rounded px-1.5 py-0.5 text-[10px] font-bold",
                    r.status === "OPEN" ? "bg-amber-400/15 text-amber-300" : "bg-emerald-500/15 text-emerald-300"
                  )}
                >
                  {r.status === "OPEN" ? "OPEN" : "REPAID"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Every money movement in the wallet, newest first, with the balance and loan after it. */
function Statement() {
  const { rows, error } = useRecords(smaApi.walletStatement);
  if (error) return <p className="text-xs text-rose-300">{error}</p>;
  if (!rows) return <p className="text-xs text-slate-400">Loading…</p>;
  if (!rows.length) return <p className="text-slate-400">Nothing yet. Add money to open the wallet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] whitespace-nowrap text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-slate-400">
          <tr>
            <th className="px-2 py-1.5">When</th>
            <th className="px-2 py-1.5">What</th>
            <th className="px-2 py-1.5 text-right">Amount</th>
            <th className="px-2 py-1.5 text-right">Free after</th>
            <th className="px-2 py-1.5 text-right">Loan after</th>
            <th className="px-2 py-1.5">Details</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((r) => {
            const plus = r.kind === "ADD" || r.kind === "LOAN";
            const minus = r.kind === "WITHDRAW" || r.kind === "REPAY";
            return (
              <tr key={r.id}>
                <td className="px-2 py-1.5 text-slate-300">{when(r.at)}</td>
                <td className={clsx("px-2 py-1.5 font-semibold", r.kind === "LOAN" ? "text-amber-300" : "text-slate-200")}>
                  {KIND_LABEL[r.kind] ?? r.kind}
                </td>
                <td className={clsx("px-2 py-1.5 text-right font-mono", plus ? "text-emerald-300" : minus ? "text-rose-300" : "text-slate-400")}>
                  {r.amount ? `${plus ? "+" : minus ? "−" : ""}${inr(r.amount)}` : "—"}
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-slate-200">{r.balance_after == null ? "—" : inr(r.balance_after)}</td>
                <td className={clsx("px-2 py-1.5 text-right font-mono", (r.loan_after ?? 0) > 0 ? "text-amber-300" : "text-slate-400")}>
                  {inr(r.loan_after ?? 0)}
                </td>
                <td className="max-w-[18rem] truncate px-2 py-1.5 text-slate-400" title={r.note}>
                  {r.kind === "LOAN" ? `Bot ${r.bot ?? 1} · ${r.symbol} ×${r.qty} @ ${inr(r.price ?? 0)} · L${r.id}` : r.note}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
