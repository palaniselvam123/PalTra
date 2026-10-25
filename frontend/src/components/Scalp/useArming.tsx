"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import { smaApi, type BotSummary } from "@/lib/smaApi";
import { api, type DeskArmedBot, type LinkServer } from "@/lib/api";
import { useServerRole } from "@/hooks/useServerRole";
import {
  ArmPicker,
  armDesks,
  backtestTerminalUrl,
  type ArmDesk,
  type ArmServerChoice,
} from "@/components/Scalp/ArmPicker";
import { ArmPrompt } from "@/components/Terminal/ArmPrompt";
import { BulkArm } from "@/components/Scalp/BulkArm";

const asSummary = (bots: DeskArmedBot[]): BotSummary[] =>
  bots.map((b) => ({ ...b, status: "", held: [], trades_today: 0, net_today: 0 }));

/**
 * Arm a stock on an SMA bot from any page (Scalp, Movers): which bot first,
 * then the prompt for quantity, stop, trail and target, then the arm itself.
 * A LIVE bot is confirmed once more before it is armed. Arming only puts the
 * stock on that bot's Trade list; it orders on the bot's next SMA cross, not now.
 *
 * Render `dialogs` once on the page. `priceOf` gives the prompt a price to show.
 *
 * On the scanner server (which never trades) the bots are the desk's: the armed chips,
 * the prompt's settings and the arm itself go through the desk link, PAPER and LIVE.
 * A LIVE bot is confirmed here first and the link refuses it without that confirmation.
 * With a backtest server set up (`backtest_arm`), the dialogs also offer "Backtest server": the
 * same arm goes to that server's PAPER copies of the bots instead, for a backtest there.
 */
export function useArming(priceOf?: (symbol: string) => number | null | undefined) {
  // Every SMA bot's Trade list (and the research desk's), so a stock can be armed on any of them.
  const [bots, setBots] = useState<BotSummary[]>([]);
  const [researchArmed, setResearchArmed] = useState<string[] | null>(null);
  const [armFor, setArmFor] = useState<{ symbol: string; source?: string | null } | null>(null);
  // After the bot is picked: quantity, stop, trail and target for that bot (ArmPrompt).
  const [armAsk, setArmAsk] = useState<{ symbol: string; desk: ArmDesk; source?: string | null } | null>(null);
  // Several ticked stocks armed on one bot with the same quantity / stop / target (BulkArm).
  const [armMany, setArmMany] = useState<{ symbols: string[]; source?: string | null } | null>(null);
  const [arming, setArming] = useState<string | null>(null);
  const [armNote, setArmNote] = useState<ReactNode | null>(null);
  // Scanner only: arm on the desk (default) or on the backtest server, and that server's Trade lists.
  const [server, setServer] = useState<LinkServer>("desk");
  const [bt, setBt] = useState<{ bots: BotSummary[]; research: string[] | null }>({ bots: [], research: null });
  const [btLoading, setBtLoading] = useState(false);
  const [btError, setBtError] = useState<string | null>(null);

  const role = useServerRole();
  const onScanner = role?.role === "scanner";
  const btUrl = onScanner && role?.backtest_arm ? (role.backtest_url ?? null) : null;

  const loadDesks = useCallback(() => {
    if (onScanner) {
      api
        .deskArmed()
        .then((d) => {
          setBots(asSummary(d.bots));
          setResearchArmed(d.research);
        })
        .catch(() => {});
      return;
    }
    smaApi.bots().then(setBots).catch(() => {});
    smaApi
      .researchConfig()
      .then((c) => setResearchArmed(c.trade_symbols ?? []))
      .catch(() => setResearchArmed(null));
  }, [onScanner]);

  // The backtest server may be stopped: this first call starts it (Fly), so it can take a while.
  const loadBacktest = useCallback(() => {
    setBtLoading(true);
    setBtError(null);
    api
      .deskArmed("backtest")
      .then((d) => {
        setBt({ bots: asSummary(d.bots), research: d.research });
        setBtError(d.error);
      })
      .catch((e: unknown) => setBtError(`Could not reach the backtest server: ${e instanceof Error ? e.message : "no answer"}`))
      .finally(() => setBtLoading(false));
  }, []);

  const deskDesks = useMemo(() => armDesks(bots, researchArmed), [bots, researchArmed]);
  // The backtest server's bots are always PAPER; shown as such whatever the copy says.
  const btDesks = useMemo(
    () => armDesks(bt.bots, bt.research).map((d) => ({ ...d, mode: "PAPER" as const })),
    [bt]
  );
  const desks = server === "backtest" ? btDesks : deskDesks;
  const serverChoice: ArmServerChoice | undefined = btUrl
    ? {
        value: server,
        onChange: (next) => {
          setServer(next);
          if (next === "backtest") loadBacktest();
        },
        loading: btLoading,
        error: server === "backtest" ? btError : null,
        url: btUrl,
      }
    : undefined;
  /** Stock → the desks it is armed on ("Bot 1", "Research", …). */
  const armed = useMemo(() => {
    const out = new Map<string, string[]>();
    for (const d of deskDesks) for (const s of d.armed) out.set(s, [...(out.get(s) ?? []), d.name]);
    return out;
  }, [deskDesks]);

  // "Arm" asks which bot first; a LIVE bot is confirmed again before it is armed.
  const reload = () => {
    loadDesks();
    if (server === "backtest") loadBacktest();
  };

  const arm = (symbol: string, source?: string | null) => {
    setArmNote(null);
    reload();
    setArmFor({ symbol, source: source ?? null });
  };
  /** Arm every ticked stock on one bot, with one set of numbers (and backtest them there). */
  const armSeveral = (symbols: string[], source?: string | null) => {
    if (!symbols.length) return;
    setArmNote(null);
    reload();
    setArmMany({ symbols, source: source ?? null });
  };
  const armOn = (symbol: string, desk: ArmDesk, source?: string | null) => {
    setArmFor(null);
    setArmAsk({ symbol, desk, source: source ?? null });
  };
  /** Runs from the prompt: LIVE is confirmed again, the prompt's changes saved, then the stock armed. */
  const armNow = async (symbol: string, desk: ArmDesk, save: () => Promise<void>, source?: string | null) => {
    const where = server;
    if (desk.mode === "LIVE" && where === "desk") {
      const ok = window.confirm(
        `Arm ${symbol} on ${desk.name} for LIVE SMA orders? ${desk.name} can buy or sell it with real money on its next SMA cross.`
      );
      if (!ok) return;
    }
    setArming(symbol);
    setArmNote(null);
    try {
      await save();
      if (onScanner) {
        await api.deskArm(
          { target: desk.target, symbol, source: source ?? null, confirm_live: where === "desk" && desk.mode === "LIVE" },
          where
        );
      } else {
        await smaApi.setTradeSymbolOn(desk.target, symbol, true, source ?? null);
      }
      if (where === "backtest" && btUrl) {
        setArmNote(
          <>
            {symbol} is armed on {desk.name} on the backtest server (practice copy).{" "}
            <a href={backtestTerminalUrl(btUrl, desk.target)} target="_blank" rel="noreferrer" className="font-semibold underline">
              Open it to run the backtest
            </a>
          </>
        );
      } else {
        setArmNote(
          `${symbol} is armed on ${desk.name}${desk.mode === "LIVE" ? " (LIVE)" : ""}. It orders on its next SMA cross, not now.`
        );
      }
    } finally {
      setArming(null);
      reload();
    }
  };

  const dialogs = (
    <>
      {armMany ? (
        <BulkArm
          symbols={armMany.symbols}
          desks={desks}
          onScanner={onScanner}
          source={armMany.source}
          onDone={reload}
          onClose={() => setArmMany(null)}
          server={serverChoice}
        />
      ) : null}
      {armFor ? (
        <ArmPicker
          symbol={armFor.symbol}
          desks={desks}
          onPick={(d) => armOn(armFor.symbol, d, armFor.source)}
          onClose={() => setArmFor(null)}
          server={serverChoice}
        />
      ) : null}
      {armAsk ? (
        <ArmPrompt
          symbol={armAsk.symbol}
          deskName={server === "backtest" ? `${armAsk.desk.name} (backtest server)` : armAsk.desk.name}
          target={armAsk.desk.target}
          live={server === "desk" && armAsk.desk.mode === "LIVE"}
          price={priceOf?.(armAsk.symbol) ?? null}
          onArm={(save) => armNow(armAsk.symbol, armAsk.desk, save, armAsk.source)}
          onClose={() => setArmAsk(null)}
          io={
            onScanner
              ? {
                  load: (t, s) => api.deskStock(t, s, server),
                  // Runs only after armNow's LIVE question was answered yes.
                  save: (t, s, body) => api.deskSaveStock(t, s, body, server === "desk" && armAsk.desk.mode === "LIVE", server),
                }
              : undefined
          }
        />
      ) : null}
    </>
  );

  return { armed, arming, armNote, arm, armSeveral, loadDesks, dialogs };
}
