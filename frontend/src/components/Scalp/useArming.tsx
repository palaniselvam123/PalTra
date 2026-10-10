"use client";

import { useCallback, useMemo, useState } from "react";
import { smaApi, type BotSummary } from "@/lib/smaApi";
import { ArmPicker, armDesks, type ArmDesk } from "@/components/Scalp/ArmPicker";
import { ArmPrompt } from "@/components/Terminal/ArmPrompt";

/**
 * Arm a stock on an SMA bot from any page (Scalp, Movers): which bot first,
 * then the prompt for quantity, stop, trail and target, then the arm itself.
 * A LIVE bot is confirmed once more before it is armed. Arming only puts the
 * stock on that bot's Trade list; it orders on the bot's next SMA cross, not now.
 *
 * Render `dialogs` once on the page. `priceOf` gives the prompt a price to show.
 */
export function useArming(priceOf?: (symbol: string) => number | null | undefined) {
  // Every SMA bot's Trade list (and the research desk's), so a stock can be armed on any of them.
  const [bots, setBots] = useState<BotSummary[]>([]);
  const [researchArmed, setResearchArmed] = useState<string[] | null>(null);
  const [armFor, setArmFor] = useState<{ symbol: string; source?: string | null } | null>(null);
  // After the bot is picked: quantity, stop, trail and target for that bot (ArmPrompt).
  const [armAsk, setArmAsk] = useState<{ symbol: string; desk: ArmDesk; source?: string | null } | null>(null);
  const [arming, setArming] = useState<string | null>(null);
  const [armNote, setArmNote] = useState<string | null>(null);

  const loadDesks = useCallback(() => {
    smaApi.bots().then(setBots).catch(() => {});
    smaApi
      .researchConfig()
      .then((c) => setResearchArmed(c.trade_symbols ?? []))
      .catch(() => setResearchArmed(null));
  }, []);

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
      await smaApi.setTradeSymbolOn(desk.target, symbol, true, source ?? null);
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
        />
      ) : null}
    </>
  );

  return { armed, arming, armNote, arm, loadDesks, dialogs };
}
