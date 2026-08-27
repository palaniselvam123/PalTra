"use client";

import { createContext, useContext } from "react";
import clsx from "clsx";
import { ArrowDownRight, ArrowUpRight, ShieldAlert, X } from "lucide-react";
import { useNotifications, type TradeNotice } from "@/hooks/useNotifications";
import { money, timeOnly } from "@/lib/format";

type Ctx = ReturnType<typeof useNotifications>;

const NotificationContext = createContext<Ctx | null>(null);

/** Lives in the root layout so the state is shared: the Navbar toggle, the
 *  WebSocket handler inside useTradingState, and the toast stack all need the
 *  same instance. Putting it in a provider also avoids opening a second
 *  WebSocket just to listen for alerts. */
export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const value = useNotifications();

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <div className="fixed bottom-20 right-5 z-50 flex flex-col gap-2 w-[min(380px,calc(100vw-2.5rem))] pointer-events-none">
        {value.toasts.map((t) => (
          <Toast key={t.id} notice={t} onDismiss={() => value.dismiss(t.id)} />
        ))}
      </div>
    </NotificationContext.Provider>
  );
}

export function useNotificationCenter(): Ctx {
  const ctx = useContext(NotificationContext);
  if (ctx) return ctx;
  // A no-op stand-in keeps consumers usable outside the provider (tests,
  // isolated rendering) instead of throwing.
  return {
    permission: "default",
    enabled: false,
    enable: async () => {},
    disable: () => {},
    notify: () => {},
    toasts: [],
    dismiss: () => {},
  };
}

function Toast({ notice, onDismiss }: { notice: TradeNotice; onDismiss: () => void }) {
  const isExit = notice.kind === "EXIT";
  const isAlert = notice.kind === "ALERT";
  const profit = (notice.pnl ?? 0) > 0;

  const accent = isAlert
    ? notice.level === "INFO"
      ? "border-profit/40"
      : "border-loss/50"
    : isExit
    ? profit
      ? "border-profit/40"
      : "border-loss/40"
    : "border-bot/40";

  return (
    <div
      className={clsx(
        "pointer-events-auto rounded-lg border bg-surface shadow-xl overflow-hidden animate-in",
        accent
      )}
    >
      <div className="px-3 py-2.5 space-y-1.5">
        <div className="flex items-start gap-2">
          <span className="mt-0.5 shrink-0">
            {isAlert ? (
              <ShieldAlert size={14} className={notice.level === "INFO" ? "text-profit" : "text-loss"} />
            ) : isExit ? (
              profit ? (
                <ArrowUpRight size={14} className="text-profit" />
              ) : (
                <ArrowDownRight size={14} className="text-loss" />
              )
            ) : (
              <ArrowUpRight size={14} className="text-bot" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium text-slate-100">{notice.title}</div>
            {notice.kind !== "ALERT" && (
              <div className="font-mono text-[11px] text-slate-400 mt-0.5">
                {notice.quantity?.toLocaleString("en-IN")} @ ₹{notice.price?.toLocaleString("en-IN")}
                {notice.pnl !== null && notice.pnl !== undefined && (
                  <span className={clsx("ml-1.5", profit ? "text-profit" : "text-loss")}>
                    {money(notice.pnl, true)}
                  </span>
                )}
              </div>
            )}
          </div>
          <button onClick={onDismiss} className="text-slate-600 hover:text-slate-300 shrink-0" aria-label="Dismiss">
            <X size={13} />
          </button>
        </div>

        {/* The reason is the point of the alert — without it "SELL 3703
            HDFCBANK" tells you nothing about why it happened. */}
        <p className="text-[11px] text-slate-400 leading-relaxed pl-6">{notice.reason}</p>
        <div className="text-[10px] text-slate-600 pl-6">
          {notice.account === "MANUAL" ? "Manual desk" : "Strategy account"} · {timeOnly(notice.at)}
        </div>
      </div>
    </div>
  );
}
