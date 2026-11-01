"use client";

import { useEffect, useState } from "react";

type Channel = { provider: string; channel_type: string; enabled: boolean; configured: boolean };
type Provider = "telegram" | "twilio" | "callmebot";

function deskOrigin(): string {
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host !== "localhost" && host !== "127.0.0.1") return window.location.origin;
  }
  return process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
}

async function desk<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${deskOrigin()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* plain text */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

function providerLabel(name: string): string {
  if (name === "twilio_voice") return "Twilio phone call";
  if (name === "telegram_quiet") return "Quiet-runner Telegram";
  if (name === "telegram_critical") return "Critical-alerts Telegram";
  if (name === "telegram_news") return "News Telegram";
  if (name === "telegram_signal") return "Signal Telegram";
  if (name === "telegram_fast") return "Fast-moves Telegram";
  return name === "telegram" ? "Telegram" : name === "twilio" ? "Twilio WhatsApp" : "CallMeBot";
}

const inputCls =
  "mt-0.5 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1.5 font-mono text-sm text-[#f8fafc] outline-none";

function ChannelForm({
  channelType,
  heading,
  description,
  channels,
  onSaved,
}: {
  channelType: "trade" | "critical";
  heading: string;
  description: string;
  channels: Channel[];
  onSaved: (ch: Channel[]) => void;
}) {
  const [provider, setProvider] = useState<Provider>(channelType === "critical" ? "twilio" : "telegram");
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [fromNumber, setFromNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const active = channels.find((r) => r.channel_type === channelType && r.enabled && r.configured);

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const saved = await desk<Channel[]>("/api/scan/channels", {
        method: "POST",
        body: JSON.stringify({
          // A Telegram token typed for critical alerts is its own bot: it must not replace the trade bot.
          provider: provider === "telegram" && channelType === "critical" ? "telegram_critical" : provider,
          channel_type: channelType,
          enabled: true,
          target: phone.trim(),
          secret: secret.trim(),
          extra: provider === "twilio" ? fromNumber.trim() : "",
        }),
      });
      onSaved(saved);
      setSecret("");
      setNote(
        provider === "telegram"
          ? "Telegram connected."
          : "WhatsApp connected."
      );
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save alerts");
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    setNote(null);
    try {
      const result = await desk<{ provider: string }>(`/api/scan/test-alert?channel_type=${channelType}`, {
        method: "POST",
      });
      const where = result.provider === "telegram" ? "Telegram" : "WhatsApp";
      setNote(`Test sent through ${result.provider}. Check ${where}.`);
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Test was not sent");
    } finally {
      setBusy(false);
    }
  };

  const assign = async (name: string) => {
    setBusy(true);
    setNote(null);
    try {
      const saved = await desk<Channel[]>(`/api/scan/channels/${name}/assign`, {
        method: "POST",
        body: JSON.stringify({ channel_type: channelType }),
      });
      onSaved(saved);
      setNote(`${providerLabel(name)} now gets ${channelType === "critical" ? "critical alerts only" : "trade alerts"}.`);
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not switch the channel");
    } finally {
      setBusy(false);
    }
  };

  const reusable = channels.filter(
    (r) =>
      r.configured && r.provider !== active?.provider && r.provider !== "twilio_voice" && !r.provider.startsWith("telegram_")
  );

  const canSave =
    provider === "telegram"
      ? secret.trim().length > 0
      : provider === "twilio"
        ? phone.trim().length > 0 && secret.trim().length > 0 && fromNumber.trim().length > 0
        : phone.trim().length > 0 && secret.trim().length > 0;

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs uppercase tracking-[0.14em] text-slate-400">{heading}</div>
        <span className={active ? "text-xs font-semibold text-[#10B981]" : "text-xs text-slate-400"}>
          {active ? `${active.provider} live` : "not connected"}
        </span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-400">{description}</p>
      {reusable.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {reusable.map((r) => (
            <button
              key={r.provider}
              type="button"
              disabled={busy}
              onClick={() => assign(r.provider)}
              className="inline-flex h-9 items-center rounded-md border border-sky-400/30 px-3 text-xs font-semibold text-sky-200 hover:bg-sky-400/10 disabled:opacity-50"
            >
              Use saved {providerLabel(r.provider)} here
            </button>
          ))}
        </div>
      )}
      <div className="mt-3 flex overflow-hidden rounded-md border border-white/10 text-xs">
        {(
          [
            ["telegram", "Telegram"],
            ["twilio", "Twilio WhatsApp"],
            ["callmebot", "CallMeBot"],
          ] as const
        ).map(([name, label]) => (
          <button
            key={name}
            type="button"
            onClick={() => setProvider(name)}
            className={
              provider === name ? "flex-1 bg-white/10 py-1.5 text-slate-100" : "flex-1 py-1.5 text-slate-400"
            }
          >
            {label}
          </button>
        ))}
      </div>

      {provider === "telegram" && (
        <ol className="mt-3 list-decimal space-y-1 pl-4 text-xs leading-relaxed text-slate-400">
          <li>In Telegram, open @BotFather and send /newbot.</li>
          <li>Copy the token it gives you into the box below.</li>
          <li>Open the bot you just created and send it any message.</li>
          <li>Tap Save. The chat is picked up from that message.</li>
        </ol>
      )}
      {provider === "twilio" && (
        <ol className="mt-3 list-decimal space-y-1 pl-4 text-xs leading-relaxed text-slate-400">
          <li>In the Twilio console, go to <strong className="text-slate-300">Messaging → Try it out → Send a WhatsApp message</strong> and follow the sandbox join steps from your phone.</li>
          <li>Paste your own phone number (with country code) into <em>Phone with country code</em>.</li>
          <li>Paste <code className="rounded bg-white/10 px-1">AccountSID:AuthToken</code> into the SID:token field.</li>
          <li>Paste the <strong className="text-slate-300">Twilio sandbox number</strong> (e.g. <code className="rounded bg-white/10 px-1">+14155238886</code>) — NOT your own number — into the from-number field.</li>
          <li>Tap Save & enable, then Send test.</li>
        </ol>
      )}
      {provider === "callmebot" && (
        <p className="mt-3 text-xs leading-relaxed text-amber-200/80">
          CallMeBot did not answer the allow-message. Leave this off unless it later replies with an API key.
        </p>
      )}

      {provider === "telegram" ? (
        <>
          <label className="mt-3 block">
            <span className="text-xs uppercase tracking-wider text-slate-400">Bot token</span>
            <input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="123456789:AA..."
              className={inputCls}
            />
          </label>
          <label className="mt-2 block">
            <span className="text-xs uppercase tracking-wider text-slate-400">Chat id, if you already have it</span>
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="Leave blank to detect it"
              className={inputCls}
            />
          </label>
        </>
      ) : (
        <>
          <label className="mt-3 block">
            <span className="text-xs uppercase tracking-wider text-slate-400">Phone with country code</span>
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+919876543210"
              className={inputCls}
            />
          </label>
          <label className="mt-2 block">
            <span className="text-xs uppercase tracking-wider text-slate-400">
              {provider === "callmebot" ? "CallMeBot API key" : "account SID:auth token"}
            </span>
            <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} className={inputCls} />
          </label>
          {provider === "twilio" && (
            <label className="mt-2 block">
              <span className="text-xs uppercase tracking-wider text-slate-400">Twilio sender number (not your phone)</span>
              <input
                value={fromNumber}
                onChange={(e) => setFromNumber(e.target.value)}
                placeholder="+14155238886"
                className={inputCls}
              />
            </label>
          )}
        </>
      )}

      <div className="sticky bottom-0 z-10 -mx-4 mt-3 flex items-center gap-2 border-t border-white/10 bg-[#151921] px-4 py-3">
        <button
          type="button"
          disabled={busy || !canSave}
          onClick={save}
          className="inline-flex h-11 items-center rounded-md bg-[#10B981] px-4 text-sm font-semibold text-[#04140d] hover:bg-emerald-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
        >
          Save & enable
        </button>
        <button
          type="button"
          disabled={busy || !active}
          onClick={test}
          className="inline-flex h-11 items-center rounded-md border border-white/15 px-4 text-sm text-slate-200 hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
        >
          Send test
        </button>
      </div>
      {note && <p className="mt-2 text-xs text-slate-400">{note}</p>}
    </section>
  );
}

type StopPrefs = { after_hours_stop_call: boolean; after_hours_stop_message: boolean };

/** When the app stops (a deploy or restart) outside market hours: ring, and/or send the message. */
function AfterHoursStop() {
  const [prefs, setPrefs] = useState<StopPrefs | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    desk<StopPrefs>("/api/scan/alert-prefs")
      .then(setPrefs)
      .catch(() => setPrefs(null));
  }, []);
  const save = async (patch: Partial<StopPrefs>) => {
    setNote(null);
    try {
      setPrefs(await desk<StopPrefs>("/api/scan/alert-prefs", { method: "PUT", body: JSON.stringify(patch) }));
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save");
    }
  };
  const row = (key: keyof StopPrefs, label: string) => (
    <label className="flex items-center gap-2 text-sm text-slate-200">
      <input
        type="checkbox"
        className="h-4 w-4 accent-emerald-500"
        checked={prefs?.[key] ?? true}
        disabled={prefs == null}
        onChange={(e) => void save({ [key]: e.target.checked })}
      />
      {label}
    </label>
  );
  return (
    <div className="mt-4 border-t border-white/10 pt-3">
      <div className="text-xs uppercase tracking-wider text-slate-400">App stopped after market hours (deploys, restarts)</div>
      <p className="mt-1 text-xs leading-relaxed text-slate-400">
        Outside 09:00–15:45 IST Mon–Fri, choose how you hear that the app stopped. In market hours you always get both.
      </p>
      <div className="mt-2 space-y-1.5">
        {row("after_hours_stop_call", "Phone call")}
        {row("after_hours_stop_message", "Telegram / WhatsApp message (critical channel, else trade)")}
      </div>
      {note && <p className="mt-1 text-xs text-rose-300">{note}</p>}
    </div>
  );
}

function CallForm({ channels, onSaved }: { channels: Channel[]; onSaved: (ch: Channel[]) => void }) {
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [fromNumber, setFromNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const active = channels.find((r) => r.channel_type === "call" && r.enabled && r.configured);
  const canSave = phone.trim() && secret.trim().includes(":") && fromNumber.trim();

  const run = async (path: string, init: RequestInit, done: (res: unknown) => string) => {
    setBusy(true);
    setNote(null);
    try {
      setNote(done(await desk<unknown>(path, init)));
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs uppercase tracking-[0.14em] text-slate-400">Phone call (bot stopped / app stopped)</div>
        <span className={active ? "text-xs font-semibold text-[#10B981]" : "text-xs text-slate-400"}>
          {active ? "on" : "not connected"}
        </span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-400">
        Twilio rings your phone and reads the alert when a bot halts or stops on its loss limit, the app shuts down, or a
        live position has no stop. Not for the normal end of the day or a Panic you pressed. Market hours only (09:00–15:45
        IST, Mon–Fri), at most one call every 5 minutes; an app stop after hours follows the switches below.
      </p>
      <ol className="mt-3 list-decimal space-y-1 pl-4 text-xs leading-relaxed text-slate-400">
        <li>
          In Twilio, open <strong className="text-slate-300">Phone Numbers → Buy a number</strong> and take the free trial
          number with Voice.
        </li>
        <li>
          On a trial account, add your own phone under <strong className="text-slate-300">Verified Caller IDs</strong>, or
          Twilio will not call it.
        </li>
        <li>Fill in the three boxes below, tap Save & enable, then Test call.</li>
      </ol>
      <label className="mt-3 block">
        <span className="text-xs uppercase tracking-wider text-slate-400">Your phone (verified, with country code)</span>
        <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+919876543210" className={inputCls} />
      </label>
      <label className="mt-2 block">
        <span className="text-xs uppercase tracking-wider text-slate-400">account SID:auth token</span>
        <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} className={inputCls} />
      </label>
      <label className="mt-2 block">
        <span className="text-xs uppercase tracking-wider text-slate-400">Your Twilio phone number (the caller)</span>
        <input
          value={fromNumber}
          onChange={(e) => setFromNumber(e.target.value)}
          placeholder="+1 Twilio number with Voice"
          className={inputCls}
        />
      </label>
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          disabled={busy || !canSave}
          onClick={() =>
            run(
              "/api/scan/channels",
              {
                method: "POST",
                body: JSON.stringify({
                  provider: "twilio_voice",
                  channel_type: "call",
                  enabled: true,
                  target: phone.trim(),
                  secret: secret.trim(),
                  extra: fromNumber.trim(),
                }),
              },
              (res) => {
                onSaved(res as Channel[]);
                setSecret("");
                return "Phone calls are on.";
              }
            )
          }
          className="inline-flex h-11 items-center rounded-md bg-[#10B981] px-4 text-sm font-semibold text-[#04140d] hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
        >
          Save & enable
        </button>
        <button
          type="button"
          disabled={busy || !active}
          onClick={() =>
            run("/api/scan/test-alert?channel_type=call", { method: "POST" }, () => "Calling your phone now.")
          }
          className="inline-flex h-11 items-center rounded-md border border-white/15 px-4 text-sm text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
        >
          Test call
        </button>
      </div>
      {note && <p className="mt-2 text-xs text-slate-400">{note}</p>}
      <AfterHoursStop />
    </section>
  );
}

/** One kind of alert's own Telegram bot (its own BotFather token and chat), so it is not lost among trade alerts. */
export const OWN_BOTS: {
  kind: "critical" | "news" | "signal" | "fast" | "quiet";
  provider: string;
  title: string;
  blurb: string;
  testNote: string;
}[] = [
  {
    kind: "critical",
    provider: "telegram_critical",
    title: "Critical alerts (own Telegram bot)",
    blurb:
      "Bot stopped, the application stopping or crashing, a live position with no stop. Until this bot is set, critical alerts go through the critical channel above, or the trade channel.",
    testNote: "Test sent. Check the critical-alerts bot.",
  },
  {
    kind: "news",
    provider: "telegram_news",
    title: "News alerts",
    blurb: "Headlines on armed / held stocks and the F&O feed (Settings → News alerts). Until this bot is set, they go to the trade alert channel.",
    testNote: "Test sent. Check the news bot.",
  },
  {
    kind: "signal",
    provider: "telegram_signal",
    title: "Signal alerts",
    blurb:
      "Heads-ups that are not orders: an SMA cross coming inside 3 minutes, an SMA 9 turn (turn alert), a 1-minute review. Until this bot is set, they go to the trade alert channel.",
    testNote: "Test sent. Check the signal bot.",
  },
  {
    kind: "fast",
    provider: "telegram_fast",
    title: "Fast-move alerts",
    blurb: "Fast price changes on armed / held stocks and in the Trend quality scan (Settings → Fast-move alerts). Until this bot is set, they go to the trade alert channel.",
    testNote: "Test sent. Check the fast-moves bot.",
  },
  {
    kind: "quiet",
    provider: "telegram_quiet",
    title: "Quiet-runner alerts (Scalp page)",
    blurb: "The Trend quality scan's 🔔 quiet runners. Until this bot is set, they go to the trade alert channel as before.",
    testNote: "Test sent. Check the quiet-runner bot.",
  },
];

function OwnBotForm({ bot, channels, onSaved }: { bot: (typeof OWN_BOTS)[number]; channels: Channel[]; onSaved: (ch: Channel[]) => void }) {
  const [secret, setSecret] = useState("");
  const [chat, setChat] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const active = channels.find((r) => r.channel_type === bot.kind && r.enabled && r.configured);
  const ownActive = active?.provider === bot.provider;

  const run = async (path: string, init: RequestInit, done: (res: unknown) => string) => {
    setBusy(true);
    setNote(null);
    try {
      setNote(done(await desk<unknown>(path, init)));
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs uppercase tracking-[0.14em] text-slate-400">{bot.title}</div>
        <span className={ownActive ? "text-xs font-semibold text-[#10B981]" : "text-xs text-slate-400"}>
          {ownActive ? "own bot live" : active ? `via ${providerLabel(active.provider)}` : "not connected · sent with trade alerts"}
        </span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-400">{bot.blurb}</p>
      <ol className="mt-3 list-decimal space-y-1 pl-4 text-xs leading-relaxed text-slate-400">
        <li>In Telegram, open @BotFather, send /newbot and copy the token (a new bot for each kind of alert).</li>
        <li>Open the new bot and send it any message (e.g. /start).</li>
        <li>Paste the token below and tap Save. The chat is picked up from that message.</li>
      </ol>
      <label className="mt-3 block">
        <span className="text-xs uppercase tracking-wider text-slate-400">Bot token</span>
        <input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder="123456789:AA..."
          className={inputCls}
        />
      </label>
      <label className="mt-2 block">
        <span className="text-xs uppercase tracking-wider text-slate-400">Chat id, if you already have it</span>
        <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="Leave blank to detect it" className={inputCls} />
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || !secret.trim()}
          onClick={() =>
            run(
              "/api/scan/channels",
              {
                method: "POST",
                body: JSON.stringify({
                  provider: bot.provider,
                  channel_type: bot.kind,
                  enabled: true,
                  target: chat.trim(),
                  secret: secret.trim(),
                }),
              },
              (res) => {
                onSaved(res as Channel[]);
                setSecret("");
                return `${bot.title} now go to this bot.`;
              }
            )
          }
          className="inline-flex h-11 items-center rounded-md bg-[#10B981] px-4 text-sm font-semibold text-[#04140d] hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
        >
          Save & enable
        </button>
        <button
          type="button"
          disabled={busy || !active}
          onClick={() => run(`/api/scan/test-alert?channel_type=${bot.kind}`, { method: "POST" }, () => bot.testNote)}
          className="inline-flex h-11 items-center rounded-md border border-white/15 px-4 text-sm text-slate-200 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-50 sm:h-9"
        >
          Send test
        </button>
        {ownActive ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(`/api/scan/channels/${bot.provider}`, { method: "DELETE" }, (res) => {
                onSaved(res as Channel[]);
                return "Removed. These alerts go with the trade alerts again.";
              })
            }
            className="inline-flex h-11 items-center rounded-md px-3 text-sm text-rose-300 hover:bg-rose-500/10 sm:h-9"
          >
            Remove this bot
          </button>
        ) : null}
      </div>
      {note && <p className="mt-2 text-xs text-slate-400">{note}</p>}
    </section>
  );
}

export function WhatsAppAlerts() {
  const [channels, setChannels] = useState<Channel[]>([]);

  useEffect(() => {
    desk<Channel[]>("/api/scan/channels")
      .then(setChannels)
      .catch(() => setChannels([]));
  }, []);

  return (
    <div className="space-y-4">
      <ChannelForm
        channelType="trade"
        heading="Trade alerts"
        description="Fills, closes, upcoming entries and warnings. Fires on every signal."
        channels={channels}
        onSaved={setChannels}
      />
      <ChannelForm
        channelType="critical"
        heading="Critical alerts only"
        description="Bot stopped, application hung, no stop on a live position. Use a different provider from trade alerts so both arrive."
        channels={channels}
        onSaved={setChannels}
      />
      {/* Each kind of alert with its own Telegram bot: critical, news, signals, fast moves, quiet runners. */}
      {OWN_BOTS.map((bot) => (
        <OwnBotForm key={bot.kind} bot={bot} channels={channels} onSaved={setChannels} />
      ))}
      <CallForm channels={channels} onSaved={setChannels} />
    </div>
  );
}

/**
 * One line for the terminal's side panel: which alert channel is on, with a
 * link to set it up. The form itself lives on the Settings page.
 */
export function AlertsStatus() {
  const [channels, setChannels] = useState<Channel[] | null>(null);
  useEffect(() => {
    desk<Channel[]>("/api/scan/channels")
      .then(setChannels)
      .catch(() => setChannels([]));
  }, []);
  const active = channels?.find((row) => row.channel_type === "trade" && row.enabled && row.configured);
  const name = active
    ? active.provider === "telegram"
      ? "Telegram"
      : active.provider === "twilio"
        ? "WhatsApp (Twilio)"
        : "WhatsApp (CallMeBot)"
    : null;
  return (
    <div className="flex items-center justify-between gap-2 rounded-xl border border-white/5 bg-[#151921] px-4 py-3 text-sm">
      <span className="flex items-center gap-2">
        <span aria-hidden className={`h-2 w-2 rounded-full ${active ? "bg-emerald-400" : "bg-slate-500"}`} />
        <span className="text-slate-300">
          Trade alerts{" "}
          <span className={active ? "font-semibold text-emerald-300" : "text-slate-400"}>
            {channels == null ? "…" : name ? `on · ${name}` : "off"}
          </span>
        </span>
      </span>
      <a href="/settings/#trade-alerts" className="text-xs font-semibold text-sky-300 underline-offset-2 hover:underline">
        {active ? "Change" : "Set up"} in Settings
      </a>
    </div>
  );
}
