"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { Brain, CheckCircle2, Loader2, Trash2, XCircle } from "lucide-react";
import { api, type AiStatus } from "@/lib/api";

const SUGGESTED_MODELS = ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o"];

const inputClass =
  "bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-bot/60";

function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className={clsx("flex items-start gap-2.5 cursor-pointer", disabled && "opacity-50 cursor-not-allowed")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={clsx(
          "mt-0.5 w-9 h-5 rounded-full transition shrink-0 relative",
          checked ? "bg-bot/70" : "bg-slate-700"
        )}
      >
        <span
          className={clsx(
            "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all",
            checked ? "left-[18px]" : "left-0.5"
          )}
        />
      </button>
      <span>
        <span className="text-xs text-slate-200">{label}</span>
        {hint && <span className="block text-[11px] text-slate-500 mt-0.5">{hint}</span>}
      </span>
    </label>
  );
}

export function AiKeyForm() {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const refresh = () => api.getAiStatus().then(setStatus).catch(() => {});

  useEffect(() => {
    refresh();
  }, []);

  const patch = async (body: any) => {
    setMessage(null);
    try {
      await api.setAiConfig(body);
      await refresh();
    } catch (e: any) {
      setMessage({ ok: false, text: e.message ?? "Could not save that setting" });
      await refresh();
    }
  };

  const saveKey = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    setMessage(null);
    try {
      await api.saveAiKey(apiKey.trim());
      setApiKey("");
      setMessage({ ok: true, text: "Key saved and encrypted. Run Test Connection to confirm it works." });
      await refresh();
    } catch (e: any) {
      setMessage({ ok: false, text: e.message ?? "Could not save the key" });
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setMessage(null);
    try {
      const res = await api.testAiKey(status?.model);
      setMessage({ ok: res.model_available, text: res.detail });
    } catch (e: any) {
      setMessage({ ok: false, text: e.message ?? "Test failed" });
    } finally {
      setTesting(false);
    }
  };

  const removeKey = async () => {
    setMessage(null);
    try {
      await api.deleteAiKey();
      setMessage({ ok: true, text: "Key deleted. The AI entry gate was switched off with it." });
      await refresh();
    } catch (e: any) {
      setMessage({ ok: false, text: e.message ?? "Could not delete the key" });
    }
  };

  return (
    <div className="rounded-lg border border-border bg-surface p-5 space-y-4">
      <div className="flex items-center gap-2">
        <Brain size={16} className="text-bot" />
        <h2 className="text-sm font-medium text-slate-200">AI Trading Expert (OpenAI)</h2>
        {status && (
          <span
            className={clsx(
              "text-[10px] px-1.5 py-0.5 rounded ml-auto",
              status.configured ? "bg-profit/15 text-profit" : "bg-slate-700/40 text-slate-400"
            )}
          >
            {status.configured ? "KEY SAVED" : "NOT CONFIGURED"}
          </span>
        )}
      </div>

      <p className="text-xs text-slate-500">
        The expert is asked for the read your ORB engine cannot produce: news flow, results and guidance,
        analyst actions, sector read-across, macro and policy, flows, and retail sentiment. It is explicitly
        instructed <span className="text-slate-300">not</span> to analyse the stock&apos;s price behaviour — no chart
        patterns, levels or indicators — because the engine already owns that and running it twice would just
        double-count the same signal.
      </p>
      <p className="text-xs text-slate-500">
        The key is Fernet-encrypted at rest, same vault as your broker credentials, and is never returned by
        any endpoint after you save it. It buys analysis only: there is no path from a model response to an
        order.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          OpenAI API Key
          <input
            type="password"
            placeholder={status?.configured ? "•••••••••• (saved — enter a new key to replace)" : "sk-…"}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className={inputClass}
          />
        </label>

        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Model
          <input
            list="ai-models"
            value={status?.model ?? ""}
            onChange={(e) => setStatus((s) => (s ? { ...s, model: e.target.value } : s))}
            onBlur={(e) => e.target.value && e.target.value !== "" && patch({ model: e.target.value })}
            className={inputClass}
          />
          <datalist id="ai-models">
            {SUGGESTED_MODELS.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </label>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={saveKey}
          disabled={saving || !apiKey.trim()}
          className="px-3 py-1.5 rounded-md bg-bot/20 text-bot text-xs font-medium hover:bg-bot/30 transition flex items-center gap-1.5 disabled:opacity-40"
        >
          {saving && <Loader2 size={13} className="animate-spin" />}
          Save Key
        </button>
        <button
          onClick={test}
          disabled={testing || !status?.configured}
          className="px-3 py-1.5 rounded-md border border-border text-xs font-medium text-slate-300 hover:bg-white/5 transition flex items-center gap-1.5 disabled:opacity-40"
        >
          {testing && <Loader2 size={13} className="animate-spin" />}
          Test Connection
        </button>
        {status?.configured && (
          <button
            onClick={removeKey}
            className="px-3 py-1.5 rounded-md border border-loss/40 text-loss text-xs font-medium hover:bg-loss/10 transition flex items-center gap-1.5 ml-auto"
          >
            <Trash2 size={12} /> Delete Key
          </button>
        )}
      </div>

      {message && (
        <div className={clsx("flex items-start gap-1.5 text-xs", message.ok ? "text-profit" : "text-loss")}>
          {message.ok ? (
            <CheckCircle2 size={13} className="mt-0.5 shrink-0" />
          ) : (
            <XCircle size={13} className="mt-0.5 shrink-0" />
          )}
          <span>{message.text}</span>
        </div>
      )}

      {status && (
        <div className="border-t border-border pt-4 space-y-3">
          <div className="text-xs font-medium text-slate-300">How the expert is used</div>

          <Toggle
            checked={status.web_search_enabled}
            onChange={(v) => patch({ web_search_enabled: v })}
            label="Search the web for current news"
            hint="On: the model retrieves real, current articles and cites them — slower and costlier per call. Off: it answers from training data only, so sentiment will be stale."
          />

          <Toggle
            checked={status.gate_enabled}
            onChange={(v) => patch({ gate_enabled: v })}
            disabled={!status.configured}
            label="Let the expert veto bot entries"
            hint={
              status.configured
                ? "The ORB bot asks the expert before each breakout entry and skips the trade if the view disagrees. The expert can only veto — it never opens a trade, and the risk engine still gates whatever it passes."
                : "Save a key first."
            }
          />

          {status.gate_enabled && (
            <div className="pl-11 space-y-3">
              <label className="text-xs text-slate-400 flex flex-col gap-1.5">
                <span>
                  Minimum conviction to allow an entry:{" "}
                  <span className="font-mono text-slate-200">{status.min_conviction}</span>
                </span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={status.min_conviction}
                  onChange={(e) => setStatus({ ...status, min_conviction: Number(e.target.value) })}
                  onMouseUp={(e) => patch({ min_conviction: Number((e.target as HTMLInputElement).value) })}
                  onTouchEnd={(e) => patch({ min_conviction: Number((e.target as HTMLInputElement).value) })}
                  className="w-full max-w-xs accent-cyan-500"
                />
                <span className="text-[11px] text-slate-500">
                  The prompt reserves conviction above 75 for cases with concrete, recent, corroborated
                  catalysts — so a high bar here will veto most signals.
                </span>
              </label>

              <Toggle
                checked={status.require_agreement}
                onChange={(v) => patch({ require_agreement: v })}
                label="Stance must agree with the signal direction"
                hint="A long breakout needs a BULLISH view, a short needs BEARISH. Off: only the conviction floor applies, so a confident view in the opposite direction would still let the trade through."
              />

              <label className="text-xs text-slate-400 flex flex-col gap-1">
                View freshness (seconds)
                <input
                  type="number"
                  min={60}
                  step={60}
                  value={status.cache_ttl_sec}
                  onChange={(e) => setStatus({ ...status, cache_ttl_sec: Number(e.target.value) })}
                  onBlur={(e) => patch({ cache_ttl_sec: Number(e.target.value) })}
                  className={`${inputClass} w-32`}
                />
                <span className="text-[11px] text-slate-500">
                  How long a view is reused before a fresh call is made. Lower means more current sentiment and
                  a bigger API bill.
                </span>
              </label>
            </div>
          )}

          {status.gate_enabled && (
            <div className="text-[11px] text-amber-400/90 bg-amber-500/10 border border-amber-500/25 rounded-md px-3 py-2">
              With the gate on, a breakout is skipped whenever no fresh view is available yet — the bot re-checks
              on the next candle rather than blocking the price feed while it waits. Expect fewer entries than
              with the gate off.
            </div>
          )}

          {(status.cached_symbols.length > 0 || status.in_flight.length > 0) && (
            <div className="text-[11px] text-slate-500">
              Cached views: {status.cached_symbols.join(", ") || "none"}
              {status.in_flight.length > 0 && ` · fetching: ${status.in_flight.join(", ")}`}
            </div>
          )}
          {/* Only meaningful once a key exists — otherwise the "no key saved"
              error from a prior call reads as a fault rather than the obvious
              consequence of not having configured anything yet. */}
          {status.configured && status.last_error && (
            <div className="text-[11px] text-loss">Last error: {status.last_error}</div>
          )}
        </div>
      )}
    </div>
  );
}
