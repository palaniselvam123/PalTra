"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import clsx from "clsx";

const WORD = "CONFIRM";

type Props = {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  /** LIVE money: the person must type CONFIRM before the button works. */
  requireTyping: boolean;
  danger?: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  requireTyping,
  danger = true,
  busy = false,
  onCancel,
  onConfirm,
}: Props) {
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    setTyped("");
    const id = setTimeout(() => (requireTyping ? inputRef.current : cancelRef.current)?.focus(), 0);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(id);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, requireTyping, onCancel]);

  if (!open) return null;
  const ready = !requireTyping || typed.trim().toUpperCase() === WORD;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-3 sm:items-center sm:p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        className={clsx(
          "w-full max-w-md rounded-xl border bg-[#151921] p-5 shadow-2xl",
          danger ? "border-rose-500/50" : "border-white/15"
        )}
      >
        <div className="flex gap-3">
          <AlertTriangle aria-hidden className={clsx("mt-0.5 shrink-0", danger ? "text-rose-400" : "text-amber-300")} size={20} />
          <div className="min-w-0 flex-1">
            <h2 id="confirm-title" className="text-[17px] font-semibold text-slate-100">
              {title}
            </h2>
            <div className="mt-2 space-y-2 text-sm leading-relaxed text-slate-300">{children}</div>
          </div>
        </div>
        {requireTyping && (
          <label className="mt-4 block text-sm text-slate-300">
            This is real money. Type <span className="font-mono font-bold text-rose-300">{WORD}</span> to continue.
            <input
              ref={inputRef}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              className="mt-2 block min-h-11 w-full rounded-md border border-white/15 bg-black/40 px-3 font-mono text-[16px] uppercase tracking-widest outline-none focus:border-rose-400"
              aria-label={`Type ${WORD}`}
            />
          </label>
        )}
        <div className="mt-5 grid grid-cols-2 gap-2 sm:flex sm:justify-end">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            className="min-h-11 rounded-md px-4 text-sm font-semibold text-slate-200 ring-1 ring-inset ring-white/15 hover:bg-white/5"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!ready || busy}
            onClick={onConfirm}
            className={clsx(
              "min-h-11 rounded-md px-4 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-40",
              danger ? "bg-rose-600 text-white hover:bg-rose-500" : "bg-amber-400 text-[#1a1203] hover:bg-amber-300"
            )}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
