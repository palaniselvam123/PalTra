"use client";

import { useEffect, useState, type ReactNode } from "react";
import { BellRing, BookOpen, Bot, HelpCircle, KeyRound, SlidersHorizontal, X } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { ApiKeyForm } from "@/components/Settings/ApiKeyForm";
import { AiKeyForm } from "@/components/Settings/AiKeyForm";
import { RiskSettings } from "@/components/Settings/RiskSettings";
import { MarketDataDiagnostics } from "@/components/Settings/MarketDataDiagnostics";
import { AllBotsGuard } from "@/components/Settings/AllBotsGuard";
import { PnlAlerts } from "@/components/Settings/PnlAlerts";
import { FastMoveAlerts } from "@/components/Settings/FastMoveAlerts";
import { FastMoverBot } from "@/components/Settings/FastMoverBot";
import { HandoffReturn } from "@/components/Settings/HandoffReturn";
import { NewsEntries } from "@/components/Settings/NewsEntries";
import { NewsAlerts } from "@/components/Settings/NewsAlerts";
import { NewsByDay } from "@/components/Settings/NewsByDay";
import { SmaStrategySettings } from "@/components/Settings/SmaStrategySettings";
import { CardHeader } from "@/components/Settings/SettingsCard";
import { groupForHash, OnThisTab, rememberedGroup, SettingsTabs, type SettingsGroup } from "@/components/Settings/SettingsTabs";
import { PlainWordsPanel } from "@/components/Scalp/GuideBar";
import { WhatsAppAlerts } from "@/components/Terminal/WhatsAppAlerts";
import { useTradingState } from "@/hooks/useTradingState";

/**
 * One group of settings at a time (the whole page was a 9,000 px scroll). A link to a
 * section (`/settings/#trade-alerts`, `#sma-strategy`, `#sma-stop`) opens its tab and lands on it.
 * Every card has the same shape (components/Settings/SettingsCard.tsx): a title with an On / Off chip, one
 * plain line, "How it works" folded away, then the controls.
 */
const GROUPS: SettingsGroup[] = [
  {
    id: "strategy",
    label: "Strategy",
    hint: "How each bot trades",
    icon: <SlidersHorizontal size={15} />,
    sections: [["sma-strategy", "SMA strategy"]],
  },
  {
    id: "bots",
    label: "Bots",
    hint: "Rules for all bots together",
    icon: <Bot size={15} />,
    sections: [
      ["all-bots-guard", "All-bots target and stop"],
      ["fast-mover-bot", "Fast movers → Bot 5"],
      ["handoff-return", "Hand-offs back"],
      ["news-entries", "News entries"],
    ],
  },
  {
    id: "alerts",
    label: "Alerts & news",
    hint: "Telegram, phone calls, news",
    icon: <BellRing size={15} />,
    sections: [
      ["trade-alerts", "Trade alerts"],
      ["pnl-alerts", "P&L alerts"],
      ["fast-moves", "Fast moves"],
      ["news-alerts", "News alerts"],
      ["news-by-day", "News on a date"],
    ],
  },
  {
    id: "account",
    label: "Account",
    hint: "Keys, data check, virtual money",
    icon: <KeyRound size={15} />,
    sections: [
      ["broker-keys", "Broker keys"],
      ["ai-key", "AI expert"],
      ["diagnostics", "Live data check"],
      ["risk", "ORB desk risk"],
    ],
  },
];

/** One plain line under the tabs: what the open tab is for. */
const TAB_INTRO: Record<string, string> = {
  strategy: "How each bot decides to buy, sell and get out. Pick the bot, change what you need, then press Save at the bottom.",
  bots: "Rules that sit above the bots and apply to Bots 1-4 (and Bot 5) together. Each bot's own way of trading is on the Strategy tab.",
  alerts: "Where and when the app messages you. Set up the Trade alerts channel first; every other alert can use it.",
  account: "Keys and checks. Broker keys first; the rest is optional.",
};

/** The words a beginner meets on this page, in the order the glossary shows them (lib/plainWords.ts). */
const SETTINGS_TERMS = [
  "bot", "paper", "live", "qty", "daily_loss", "stop", "trail", "target", "square_off", "cutoff", "candle", "closed_candle",
  "sma", "cross", "atr", "vwap", "rsi", "adx", "volume", "bollinger", "supertrend", "ema", "macd", "cvd", "slope", "gap_pct",
  "gap_mode", "flip", "join_trend", "recovery", "handoff", "review", "heikin", "pivot", "pullback", "backtest", "arm", "fno",
] as const;

const START_KEY = "settings.starthere.v1";

/** Three things to set first, for someone who has never opened this page; "Hide" is remembered in this browser. */
function StartHere({ onPick }: { onPick: (groupId: string, hash: string) => void }) {
  const [hidden, setHidden] = useState(true);
  useEffect(() => {
    try {
      setHidden(localStorage.getItem(START_KEY) === "hidden");
    } catch {
      setHidden(false);
    }
  }, []);
  if (hidden) return null;
  const hide = () => {
    setHidden(true);
    try {
      localStorage.setItem(START_KEY, "hidden");
    } catch {
      /* private window */
    }
  };
  const steps: { n: number; title: string; text: string; group: string; hash: string; label: string }[] = [
    {
      n: 1,
      title: "Connect Groww",
      text: "Paste your Groww API key, secret and TOTP secret, then press Connect Live Data. Without them the desk runs on made-up prices.",
      group: "account",
      hash: "broker-keys",
      label: "Broker keys",
    },
    {
      n: 2,
      title: "Set the size, the stop and the daily loss",
      text: "How many shares per trade, a stop on every trade, and the most the bot may lose in a day. Keep the bot in PAPER (practice money) until a backtest and a few practice days look right.",
      group: "strategy",
      hash: "sma-stop",
      label: "Stop-loss and target",
    },
    {
      n: 3,
      title: "Get told on Telegram",
      text: "Make a Telegram bot with BotFather, paste its token and your chat id in Trade alerts, press Test. Every fill, close and warning then reaches your phone.",
      group: "alerts",
      hash: "trade-alerts",
      label: "Trade alerts",
    },
  ];
  return (
    <section aria-labelledby="start-here-title" className="terminal-dark rounded-xl border border-sky-400/30 bg-sky-400/[0.05] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="start-here-title" className="text-sm font-semibold text-slate-100">New here? Three things to set first</h2>
          <p className="mt-0.5 text-xs text-slate-400">Everything else can stay at its default until you have traded a few practice days.</p>
        </div>
        <button
          type="button"
          onClick={hide}
          className="inline-flex min-h-8 shrink-0 items-center gap-1 rounded-md px-2 text-xs font-semibold text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
        >
          <X size={13} aria-hidden /> Hide
        </button>
      </div>
      <ol className="mt-3 grid gap-2 md:grid-cols-3">
        {steps.map((s) => (
          <li key={s.n} className="flex gap-2.5 rounded-lg border border-white/10 bg-black/20 p-3">
            <span aria-hidden className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sky-500 text-xs font-bold text-white">
              {s.n}
            </span>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-slate-100">{s.title}</div>
              <p className="mt-0.5 text-xs leading-snug text-slate-300">{s.text}</p>
              <button
                type="button"
                onClick={() => onPick(s.group, s.hash)}
                className="mt-2 inline-flex min-h-8 items-center rounded-md px-2.5 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
              >
                Open {s.label} →
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function SettingsPage() {
  const { connected, summary, summaryLoad, killSwitchActive, killSwitch, resetKillSwitch, feed, setFeed, bot } =
    useTradingState();
  const [group, setGroup] = useState<SettingsGroup>(GROUPS[0]);
  const [words, setWords] = useState(false);

  // The tab in the address wins, then the last one used, then Strategy.
  useEffect(() => {
    const fromHash = () => groupForHash(GROUPS, window.location.hash);
    setGroup(fromHash() ?? rememberedGroup(GROUPS) ?? GROUPS[0]);
    const onHash = () => {
      const g = fromHash();
      if (g) setGroup(g);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // A section link lands on its section once the tab is on screen (the SMA form scrolls itself once loaded).
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id || id === group.id || id.startsWith("sma-")) return;
    requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView());
  }, [group]);

  const pick = (g: SettingsGroup) => {
    setGroup(g);
    const url = new URL(window.location.href);
    url.hash = g.id;
    window.history.replaceState(null, "", url.toString());
    window.scrollTo({ top: 0 });
  };

  // "Open Broker keys" from the start-here strip: the tab, then the section (hashchange opens an SMA group too).
  const jump = (groupId: string, hash: string) => {
    const g = GROUPS.find((x) => x.id === groupId);
    if (!g) return;
    setGroup(g);
    const url = new URL(window.location.href);
    url.hash = hash;
    window.history.replaceState(null, "", url.toString());
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById(hash)?.scrollIntoView({ block: "start" })));
  };

  let body: ReactNode;
  switch (group.id) {
    case "bots":
      body = (
        <>
          <AllBotsGuard />
          <FastMoverBot />
          <HandoffReturn />
          <NewsEntries />
        </>
      );
      break;
    case "alerts":
      body = (
        <>
          <section id="trade-alerts" aria-labelledby="trade-alerts-title" className="terminal-dark scroll-mt-24 rounded-xl border border-white/10 bg-[#151921] p-4 sm:p-5">
            <CardHeader
              id="trade-alerts"
              title="Trade alerts: where the bots message you"
              tier="start"
              summary="A Telegram bot for the trades (fills, closes, warnings), another for critical problems, and optional ones for news, signals, fast moves and quiet runners. Set up the trade channel first; the other alerts fall back to it."
              help={
                <>
                  On Telegram, open BotFather, send /newbot and copy the token. Start a chat with your new bot so it may message you,
                  find your chat id, paste both here and press Test. A second bot (and chat) for critical alerts means a bot-down
                  message is never buried under fills. The phone-call card (Twilio) rings for critical alerts and the alerts that
                  tick &quot;phone call&quot;; calls ring only 09:00-15:45 on weekdays and at most once every 5 minutes.
                </>
              }
            />
            <div className="mt-3">
              <WhatsAppAlerts />
            </div>
          </section>
          <PnlAlerts />
          <FastMoveAlerts />
          <NewsAlerts />
          <NewsByDay />
        </>
      );
      break;
    case "account":
      body = (
        // The smaller forms sit two to a row on a wide screen instead of stretching across it.
        <div className="grid items-start gap-4 xl:grid-cols-2">
          <ApiKeyForm />
          <AiKeyForm />
          <MarketDataDiagnostics />
          <RiskSettings />
        </div>
      );
      break;
    default:
      body = <SmaStrategySettings />;
  }

  return (
    <div>
      <Navbar
        connected={connected}
        totalPnl={summaryLoad === "ok" ? summary.total_pnl : null}
        killSwitchActive={killSwitchActive}
        onKillSwitch={killSwitch}
        onResetKillSwitch={resetKillSwitch}
        feed={feed}
        onFeedChanged={setFeed}
        botRunning={bot?.enabled ?? false}
      />
      <main className="mx-auto max-w-[1440px] space-y-4 px-4 py-6 lg:px-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
            <p className="mt-0.5 max-w-3xl text-sm text-slate-400">
              Everything the bots and the alerts follow. Change a card, then press its Save. Nothing here places an order by itself.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <a
              href="/learn/"
              className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-2.5 font-semibold text-sky-300 ring-1 ring-inset ring-sky-400/40 hover:bg-sky-500/10"
            >
              <BookOpen size={13} aria-hidden /> Guides (Learn page)
            </a>
            <button
              type="button"
              onClick={() => setWords(true)}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-2.5 font-semibold text-amber-300 ring-1 ring-inset ring-amber-400/40 hover:bg-amber-500/10"
            >
              <HelpCircle size={13} aria-hidden /> What do these words mean?
            </button>
          </div>
        </div>
        {words ? <PlainWordsPanel terms={SETTINGS_TERMS} onClose={() => setWords(false)} /> : null}
        <StartHere onPick={jump} />
        <SettingsTabs groups={GROUPS} active={group.id} onPick={pick} />
        <div role="tabpanel" id={group.id} aria-label={group.label} className="space-y-4">
          {TAB_INTRO[group.id] ? <p className="max-w-3xl text-sm text-slate-300">{TAB_INTRO[group.id]}</p> : null}
          <OnThisTab sections={group.sections} />
          {body}
        </div>
      </main>
    </div>
  );
}
