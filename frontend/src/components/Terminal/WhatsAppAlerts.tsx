"use client";

import { useEffect, useState } from "react";

type Channel = { provider: string; enabled: boolean; configured: boolean };

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

export function WhatsAppAlerts() {
  const [provider, setProvider] = useState<"callmebot" | "twilio">("callmebot");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [fromNumber, setFromNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const active = channels.find((row) => row.enabled && row.configured);

  const load = () => {
    desk<Channel[]>("/api/scan/channels")
      .then(setChannels)
      .catch(() => setChannels([]));
  };

  useEffect(() => {
    load();
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
      setNote("WhatsApp is on. Fills and closes will message this phone.");
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Could not save WhatsApp");
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    setNote(null);
    try {
      const result = await desk<{ provider: string }>("/api/scan/test-alert", { method: "POST" });
      setNote(`Test sent through ${result.provider}. Check WhatsApp.`);
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Test was not sent");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-xl border border-white/5 bg-[#151921] p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-[0.14em] text-slate-500">WhatsApp alerts</div>
        <span className={active ? "text-[10px] font-semibold text-[#10B981]" : "text-[10px] text-slate-500"}>
          {active ? `${active.provider} live` : "not connected"}
        </span>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
        A fill, stop, cross, or square-off sends a WhatsApp. For CallMeBot, message +34 623 75 84 18 with
        “I allow callmebot to send me messages”, then paste the key it replies with.
      </p>
      <div className="mt-3 flex overflow-hidden rounded-md border border-white/10 text-[11px]">
        {(["callmebot", "twilio"] as const).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setProvider(name)}
            className={
              provider === name
                ? "flex-1 bg-white/10 py-1.5 text-slate-100"
                : "flex-1 py-1.5 text-slate-500"
            }
          >
            {name === "callmebot" ? "CallMeBot" : "Twilio"}
          </button>
        ))}
      </div>
      <label className="mt-3 block">
        <span className="text-[10px] uppercase tracking-wider text-slate-500">Phone with country code</span>
        <input
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="+919876543210"
          className="mt-0.5 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1.5 font-mono text-sm text-[#f8fafc] outline-none"
        />
      </label>
      <label className="mt-2 block">
        <span className="text-[10px] uppercase tracking-wider text-slate-500">
          {provider === "callmebot" ? "CallMeBot API key" : "account SID:auth token"}
        </span>
        <input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          className="mt-0.5 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1.5 font-mono text-sm text-[#f8fafc] outline-none"
        />
      </label>
      {provider === "twilio" && (
        <label className="mt-2 block">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Twilio WhatsApp from number</span>
          <input
            value={fromNumber}
            onChange={(e) => setFromNumber(e.target.value)}
            placeholder="+14155238886"
            className="mt-0.5 w-full rounded-md border border-white/10 bg-black/40 px-2 py-1.5 font-mono text-sm text-[#f8fafc] outline-none"
          />
        </label>
      )}
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          disabled={busy || !phone.trim() || !secret.trim()}
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
