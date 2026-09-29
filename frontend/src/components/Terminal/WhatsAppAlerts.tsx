"use client";

import { useEffect, useState } from "react";

type Channel = { provider: string; enabled: boolean; configured: boolean };
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

const inputCls =
  "mt-0.5 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1.5 font-mono text-sm text-[#f8fafc] outline-none";

export function WhatsAppAlerts() {
  const [provider, setProvider] = useState<Provider>("telegram");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [fromNumber, setFromNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const active = channels.find((row) => row.enabled && row.configured);

  useEffect(() => {
    desk<Channel[]>("/api/scan/channels")
      .then(setChannels)
      .catch(() => setChannels([]));
  }, []);

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const saved = await desk<Channel[]>("/api/scan/channels", {
        method: "POST",
        body: JSON.stringify({
          provider,
          enabled: true,
          target: phone.trim(),
          secret: secret.trim(),
          extra: provider === "twilio" ? fromNumber.trim() : "",
        }),
      });
      setChannels(saved);
      setSecret("");
      setNote(
        provider === "telegram"
          ? "Telegram is on. Fills and closes will arrive in that chat."
          : "WhatsApp is on. Fills and closes will message this phone."
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
      const result = await desk<{ provider: string }>("/api/scan/test-alert", { method: "POST" });
      const where = result.provider === "telegram" ? "Telegram" : "WhatsApp";
      setNote(`Test sent through ${result.provider}. Check ${where}.`);
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Test was not sent");
    } finally {
      setBusy(false);
    }
  };

  const canSave =
    provider === "telegram" ? secret.trim().length > 0 : phone.trim().length > 0 && secret.trim().length > 0;

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-[0.14em] text-slate-500">Trade alerts</div>
        <span className={active ? "text-[10px] font-semibold text-[#10B981]" : "text-[10px] text-slate-500"}>
          {active ? `${active.provider} live` : "not connected"}
        </span>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
        CallMeBot is not replying, so it cannot give a key. Use Telegram. It answers as soon as you create a bot.
        Twilio still sends real WhatsApp if you already have an account.
      </p>
      <div className="mt-3 flex overflow-hidden rounded-md border border-white/10 text-[11px]">
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
              provider === name ? "flex-1 bg-white/10 py-1.5 text-slate-100" : "flex-1 py-1.5 text-slate-500"
            }
          >
            {label}
          </button>
        ))}
      </div>

      {provider === "telegram" && (
        <ol className="mt-3 list-decimal space-y-1 pl-4 text-[11px] leading-relaxed text-slate-400">
          <li>In Telegram, open @BotFather and send /newbot.</li>
          <li>Copy the token it gives you into the box below.</li>
          <li>Open the bot you just created and send it any message.</li>
          <li>Tap Save. The chat is picked up from that message.</li>
        </ol>
      )}
      {provider === "twilio" && (
        <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
          In the Twilio WhatsApp sandbox, send the join code from your phone to the sandbox number. Then paste your
          number, the account SID and auth token as SID:token, and the sandbox from-number.
        </p>
      )}
      {provider === "callmebot" && (
        <p className="mt-3 text-[11px] leading-relaxed text-amber-200/80">
          CallMeBot did not answer the allow-message. Leave this off unless it later replies with an API key.
        </p>
      )}

      {provider === "telegram" ? (
        <>
          <label className="mt-3 block">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Bot token</span>
            <input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="123456789:AA..."
              className={inputCls}
            />
          </label>
          <label className="mt-2 block">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Chat id, if you already have it</span>
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
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Phone with country code</span>
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+919876543210"
              className={inputCls}
            />
          </label>
          <label className="mt-2 block">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              {provider === "callmebot" ? "CallMeBot API key" : "account SID:auth token"}
            </span>
            <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} className={inputCls} />
          </label>
          {provider === "twilio" && (
            <label className="mt-2 block">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">Twilio WhatsApp from number</span>
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

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          disabled={busy || !canSave}
          onClick={save}
          className="rounded-md bg-[#10B981] px-3 py-1.5 text-xs font-semibold text-[#04140d] disabled:opacity-40"
        >
          Save & enable
        </button>
        <button
          type="button"
          disabled={busy || !active}
          onClick={test}
          className="rounded-md border border-white/10 px-3 py-1.5 text-xs text-slate-300 disabled:opacity-40"
        >
          Send test
        </button>
      </div>
      {note && <p className="mt-2 text-[11px] text-slate-400">{note}</p>}
    </section>
  );
}
