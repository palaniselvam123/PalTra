"use client";

import { useCallback, useMemo, useState } from "react";
import { smaApi, type BotSummary } from "@/lib/smaApi";
import { api } from "@/lib/api";
import { useServerRole } from "@/hooks/useServerRole";
import { ArmPicker, armDesks, type ArmDesk } from "@/components/Scalp/ArmPicker";
import { ArmPrompt } from "@/components/Terminal/ArmPrompt";
import { BulkArm } from "@/components/Scalp/BulkArm";

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
  const [armNote, setArmNote] = useState<string | null>(null);

  const role = useServerRole();
  const onScanner = role?.role === "scanner";

  const loadDesks = useCallback(() => {
    if (onScanner) {
      api
        .deskArmed()
        .then((d) => {
          setBots(d.bots.map((b) => ({ ...b, status: "", held: [], trades_today: 0, net_today: 0 })));
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

  const desks = useMemo(() => armDesks(bots, researchArmed), [bots, researchArmed]);
  /** Stock → the desks it is armed on ("Bot 1", "Research", …). */
  const armed = useMemo(() => {
    const out = new Map<string, string[]>();
    for (const d of desks) for (const s of d.armed) out.set(s, [...(out.get(s) ?? []), d.name]);
    return out;
  }, [desks]);

  // "Arm" asks which bot first; a LIVE bot is confirmed again before it is armed.
  const arm = (symbol: string, source?: string | null) => {
    setArmNote(null);
    loadDesks();
    setArmFor({ symbol, source: source ?? null });
  };
  /** Arm every ticked stock on one bot, with one set of numbers (and backtest them there). */
  const armSeveral = (symbols: string[], source?: string | null) => {
    if (!symbols.length) return;
    setArmNote(null);
    loadDesks();
    setArmMany({ symbols, source: source ?? null });
  };
  const armOn = (symbol: string, desk: ArmDesk, source?: string | null) => {
    setArmFor(null);
    setArmAsk({ symbol, desk, source: source ?? null });
  };
  /** Runs from the prompt: LIVE is confirmed again, the prompt's changes saved, then the stock armed. */
  const armNow = async (symbol: string, desk: ArmDesk, save: () => Promise<void>, source?: string | null) => {
    if (desk.mode === "LIVE") {
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
        await api.deskArm({ target: desk.target, symbol, source: source ?? null, confirm_live: desk.mode === "LIVE" });
      } else {
        await smaApi.setTradeSymbolOn(desk.target, symbol, true, source ?? null);
      }
      setArmNote(
        `${symbol} is armed on ${desk.name}${desk.mode === "LIVE" ? " (LIVE)" : ""}. It orders on its next SMA cross, not now.`
      );
    } finally {
      setArming(null);
      loadDesks();
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
          onDone={loadDesks}
          onClose={() => setArmMany(null)}
        />
      ) : null}
      {armFor ? (
        <ArmPicker
          symbol={armFor.symbol}
          desks={desks}
          onPick={(d) => armOn(armFor.symbol, d, armFor.source)}
          onClose={() => setArmFor(null)}
        />
      ) : null}
      {armAsk ? (
        <ArmPrompt
          symbol={armAsk.symbol}
          deskName={armAsk.desk.name}
          target={armAsk.desk.target}
          live={armAsk.desk.mode === "LIVE"}
          price={priceOf?.(armAsk.symbol) ?? null}
          onArm={(save) => armNow(armAsk.symbol, armAsk.desk, save, armAsk.source)}
          onClose={() => setArmAsk(null)}
          io={
            onScanner
              ? {
                  load: (t, s) => api.deskStock(t, s),
                  // Runs only after armNow's LIVE question was answered yes.
                  save: (t, s, body) => api.deskSaveStock(t, s, body, armAsk.desk.mode === "LIVE"),
                }
              : undefined
          }
        />
      ) : null}
    </>
  );

  return { armed, arming, armNote, arm, armSeveral, loadDesks, dialogs };
}
