"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { CheckCircle2, XCircle, Loader2, LogIn } from "lucide-react";
import { api } from "@/lib/api";

type CredentialStatus = {
  broker: string;
  configured: boolean;
  token_valid: boolean;
  token_expires_at: string | null;
};

const BROKERS = ["groww", "zerodha", "angelone"] as const;

export function ApiKeyForm() {
  const [broker, setBroker] = useState<(typeof BROKERS)[number]>("groww");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [totpSecret, setTotpSecret] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testOk, setTestOk] = useState<boolean | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginResult, setLoginResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [saved, setSaved] = useState<CredentialStatus | null>(null);

  // Without this the form always renders blank, so stored credentials look
  // lost after every refresh — the fields are write-only by design (the API
  // never returns a decrypted key), so the badge is the only evidence.
  const refreshSaved = (which: string) =>
    api
      .getCredentialStatus(which)
      .then(setSaved)
      .catch(() => setSaved(null));

  useEffect(() => {
    refreshSaved(broker);
  }, [broker]);

  const save = async () => {
    setStatus("saving");
    try {
      await api.saveCredentials({ broker, api_key: apiKey, api_secret: apiSecret, totp_secret: totpSecret });
      setStatus("saved");
      await refreshSaved(broker);
    } catch {
      setStatus("error");
    }
  };

  const testConnection = async () => {
    setTestResult(null);
    setTestOk(null);
    try {
      const res = await api.testTotp(broker);
      setTestOk(true);
      setTestResult(`TOTP secret valid — sample code ${res.sample_code}`);
    } catch (e: any) {
      setTestOk(false);
      setTestResult(e.message ?? "Test failed");
    }
  };

  const doLogin = async () => {
    setLoggingIn(true);
    setLoginResult(null);
    try {
      const res = await api.login(broker);
      setLoginResult({
        ok: true,
        message: `Connected. Token valid until ${new Date(res.token_expires_at).toLocaleString()}. You can now switch the data source to LIVE NSE.`,
      });
      await refreshSaved(broker);
    } catch (e: any) {
      setLoginResult({ ok: false, message: e.message ?? "Login failed" });
    } finally {
      setLoggingIn(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-surface p-5 space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-medium text-slate-200">Broker API Keys &amp; Authentication Vault</h2>
        <span
          className={clsx(
            "text-[10px] px-1.5 py-0.5 rounded ml-auto",
            saved?.configured ? "bg-profit/15 text-profit" : "bg-slate-700/40 text-slate-400"
          )}
        >
          {saved?.configured ? "KEYS SAVED" : "NOT CONFIGURED"}
        </span>
      </div>

      {saved?.configured && (
        <div className="text-[11px] text-slate-400 bg-base border border-border rounded-md px-3 py-2 space-y-0.5">
          <div>
            Credentials for <span className="text-slate-200">{saved.broker}</span> are stored and encrypted. The
            fields below stay blank on purpose — a saved key is never sent back to the browser. Fill them in only
            to replace it.
          </div>
          <div>
            Broker session:{" "}
            {saved.token_valid ? (
              <span className="text-profit">active</span>
            ) : (
              <span className="text-amber-400">not connected — click Connect Live Data</span>
            )}
            {saved.token_expires_at && (
              <span className="text-slate-500">
                {" "}
                · token expires {new Date(saved.token_expires_at).toLocaleString()}
              </span>
            )}
          </div>
        </div>
      )}
      <p className="text-xs text-slate-500">
        Credentials are encrypted at rest (Fernet) before being written to the local database. Groww is the only broker
        with a live adapter — Zerodha/Angel One store credentials but have no login implementation yet.
      </p>
      <p className="text-xs text-slate-500">
        These keys are used for <span className="text-slate-300">market data only</span>. Order placement stays
        simulated: the app never sends an order to your broker, so a valid key cannot spend real money here.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Broker
          <select
            value={broker}
            onChange={(e) => setBroker(e.target.value as any)}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200"
          >
            {BROKERS.map((b) => (
              <option key={b} value={b}>
                {b === "groww" ? "Groww" : b === "zerodha" ? "Zerodha (KiteConnect)" : "Angel One (SmartAPI)"}
              </option>
            ))}
          </select>
        </label>
        <div />
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          API Key
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200"
          />
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          API Secret
          <input
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200"
          />
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1 col-span-2">
          TOTP Secret Key (QR secret)
          <input
            type="password"
            value={totpSecret}
            onChange={(e) => setTotpSecret(e.target.value)}
            className="bg-base border border-border rounded-md px-2 py-1.5 text-sm text-slate-200"
          />
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={status === "saving"}
          className="px-3 py-1.5 rounded-md bg-bot/20 text-bot text-xs font-medium hover:bg-bot/30 transition flex items-center gap-1.5"
        >
          {status === "saving" && <Loader2 size={13} className="animate-spin" />}
          Save Credentials
        </button>
        <button
          onClick={testConnection}
          className="px-3 py-1.5 rounded-md border border-border text-xs font-medium text-slate-300 hover:bg-white/5 transition"
        >
          Test Connection
        </button>
        <button
          onClick={doLogin}
          disabled={loggingIn}
          className="px-3 py-1.5 rounded-md bg-profit/20 text-profit text-xs font-medium hover:bg-profit/30 transition flex items-center gap-1.5"
        >
          {loggingIn ? <Loader2 size={13} className="animate-spin" /> : <LogIn size={13} />}
          Connect Live Data
        </button>
        {status === "saved" && <span className="text-xs text-profit">Saved</span>}
        {status === "error" && <span className="text-xs text-loss">Failed to save</span>}
      </div>

      {loginResult && (
        <div className={`flex items-start gap-1.5 text-xs ${loginResult.ok ? "text-profit" : "text-loss"}`}>
          {loginResult.ok ? <CheckCircle2 size={13} className="mt-0.5 shrink-0" /> : <XCircle size={13} className="mt-0.5 shrink-0" />}
          <span>{loginResult.message}</span>
        </div>
      )}

      {testResult && (
        <div className={`flex items-center gap-1.5 text-xs ${testOk ? "text-profit" : "text-loss"}`}>
          {testOk ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
          {testResult}
        </div>
      )}
    </div>
  );
}
