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
          provider,
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

  const reusable = channels.filter((r) => r.configured && r.provider !== active?.provider);

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
