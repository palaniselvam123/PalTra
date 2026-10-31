"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { Bot, Check, Copy, Eraser, HelpCircle, Loader2, Maximize2, MessageSquare, Minimize2, Send, User, X } from "lucide-react";
import { api, type ChatProposal, type ChatTurn } from "@/lib/api";
import { chatScreen } from "@/lib/chatScreen";
import { ChatMarkdown } from "./ChatMarkdown";
import { ChatProposalCard } from "./ChatProposalCard";

type Facts = Record<string, unknown>;
type Shown = ChatTurn & { proposals?: ChatProposal[]; at?: number; facts?: Facts };

/** Quick follow-ups offered under an answer, picked from what the question was about. */
function followUps(question: string, reply: string): string[] {
  const q = `${question} ${reply}`.toLowerCase();
  const out: string[] = [];
  if (/cross|reverse|not (start|take|enter)|why (did|didn't|didnt|is|isn)/.test(q)) out.push("What would have made it trade there?");
  if (/trade|p&l|pnl|loss|profit/.test(q)) out.push("Show the arithmetic for that trade");
  if (/strateg|setting|stop|filter|check|candle/.test(q)) out.push("Which of these settings would you change, and why?");
  if (/yesterday|last week|past/.test(q)) out.push("How did those settings do over the last days?");
  out.push("What else is recorded about this?");
  return Array.from(new Set(out)).slice(0, 3);
}

function factsLine(f: Facts | undefined): string | null {
  if (!f) return null;
  const parts: string[] = [];
  if (typeof f.sma_bots === "number" && f.sma_bots > 0) parts.push(`${f.sma_bots} bot${f.sma_bots === 1 ? "" : "s"}`);
  if (typeof f.sma_trades_today === "number") parts.push(`${f.sma_trades_today} trade${f.sma_trades_today === 1 ? "" : "s"} today`);
  if (typeof f.sma_recent_trades === "number" && f.sma_recent_trades > 0) parts.push(`${f.sma_recent_trades} past`);
  if (f.screen) parts.push("this page");
  return parts.length ? `Read: ${parts.join(" · ")}` : null;
}

function clock(at?: number): string {
  if (!at) return "";
  return new Date(at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
}

/** Floating "ask the bot" panel, mounted in the root layout so it follows the
 *  user across Dashboard, Reports and Settings — the questions it answers
 *  ("why did this lose money?") get asked from all three. */
export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [wide, setWide] = useState(false);
  const [status, setStatus] = useState<{ configured: boolean; model: string; suggestions: string[] } | null>(null);
  const [messages, setMessages] = useState<Shown[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showStarters, setShowStarters] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const pathname = usePathname();

  useEffect(() => {
    if (open && !status) api.getChatStatus().then(setStatus).catch(() => {});
  }, [open, status]);

  useEffect(() => {
    try {
      setWide(window.localStorage.getItem("chat.wide") === "1");
    } catch {
      /* no storage: default width */
    }
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open, busy]);

  const toggleWide = () => {
    setWide((v) => {
      try {
        window.localStorage.setItem("chat.wide", v ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !v;
    });
  };

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;

    // Snapshot the history BEFORE appending: the backend wants prior turns,
    // and the new question is sent separately as `message`.
    const priorTurns = messages;
    setMessages([...priorTurns, { role: "user", content: question, at: Date.now() }]);
    setDraft("");
    setShowStarters(false);
    setBusy(true);
    setError(null);

    try {
      const history = priorTurns.map(({ role, content }) => ({ role, content }));
      const res = await api.askChat(question, history, pathname ?? undefined, chatScreen());
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.reply, proposals: res.proposals, at: Date.now(), facts: res.facts_summary },
      ]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not reach the assistant");
    } finally {
      setBusy(false);
    }
  };

  const copy = async (i: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(i);
      setTimeout(() => setCopied((c) => (c === i ? null : c)), 1500);
    } catch {
      /* clipboard refused: nothing to show */
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        aria-label="Ask the bot about its trades"
        title="Ask the bot"
        className="group fixed bottom-4 right-4 z-40 flex h-12 min-w-12 items-center justify-center rounded-full bg-bot px-3.5 text-sm font-semibold text-slate-900 shadow-lg shadow-bot/20 transition-all hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
      >
        <MessageSquare size={18} aria-hidden />
        {/* Icon only, so it does not sit over the cards at the right edge; the label slides out on hover or focus. */}
        <span className="max-w-0 overflow-hidden whitespace-nowrap transition-all duration-200 group-hover:ml-2 group-hover:max-w-[8rem] group-focus-visible:ml-2 group-focus-visible:max-w-[8rem]">
          Ask the bot
        </span>
      </button>
    );
  }

  const starters = status?.suggestions ?? [];
  const lastUser = [...messages].reverse().find((m) => m.role === "user");

  return (
    <div
      role="dialog"
      aria-label="Ask the bot"
      className={clsx(
        "fixed bottom-5 right-5 z-40 flex flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-2xl",
        wide
          ? "w-[min(760px,calc(100vw-2.5rem))] max-h-[min(860px,calc(100vh-3rem))]"
          : "w-[min(440px,calc(100vw-2.5rem))] max-h-[min(680px,calc(100vh-3rem))]"
      )}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-bot/20 text-bot">
          <Bot size={15} />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-200">Ask the bot</div>
          <div className="truncate text-[10px] text-slate-500">
            {status?.model ? `${status.model} · ` : ""}reads your trades, every bot and this page
          </div>
        </div>
        <div className="ml-auto flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => setShowStarters((v) => !v)}
            title="Example questions"
            aria-label="Example questions"
            className="rounded p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-300"
          >
            <HelpCircle size={14} />
          </button>
          <button
            type="button"
            onClick={toggleWide}
            title={wide ? "Narrow panel" : "Wide panel"}
            aria-label={wide ? "Narrow panel" : "Wide panel"}
            className="hidden rounded p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-300 sm:inline-flex"
          >
            {wide ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setMessages([]);
                setError(null);
              }}
              title="Clear conversation"
              aria-label="Clear conversation"
              className="rounded p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-300"
            >
              <Eraser size={14} />
            </button>
          )}
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="rounded p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-300"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {status && !status.configured ? (
        <div className="space-y-2 px-4 py-6 text-center">
          <p className="text-xs text-slate-500">
            This assistant explains your bot&apos;s own trades — why it entered, why it exited, why a cross was not
            traded, and where the P&amp;L went — and changes settings from plain words. It needs the same OpenAI key
            as the AI expert.
          </p>
          <Link
            href="/settings"
            onClick={() => setOpen(false)}
            className="inline-block rounded-md bg-bot/20 px-3 py-1.5 text-xs font-medium text-bot transition hover:bg-bot/30"
          >
            Add a key in Settings
          </Link>
        </div>
      ) : (
        <>
          <div ref={scrollRef} className="min-h-[200px] flex-1 space-y-3 overflow-y-auto px-4 py-3">
            {(messages.length === 0 || showStarters) && (
              <div className="space-y-3">
                {messages.length === 0 && (
                  <div className="rounded-lg border border-border bg-base/60 p-3 text-xs text-slate-400">
                    <p className="text-slate-300">I answer from the record, never from guesswork:</p>
                    <ul className="mt-1.5 space-y-1">
                      <li>
                        <span className="text-slate-200">Why a stock did or did not trade</span> at a time: every SMA cross of the day
                        and what each bot did on it.
                      </li>
                      <li>
                        <span className="text-slate-200">Which strategy a past trade used</span>: the settings saved at its entry.
                      </li>
                      <li>
                        <span className="text-slate-200">Change settings in plain words</span>: you get a card with old → new and
                        tap Apply. Never an order.
                      </li>
                    </ul>
                  </div>
                )}
                <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                  {starters.map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => send(q)}
                      className="rounded-md border border-border px-2.5 py-1.5 text-left text-xs text-slate-300 transition hover:border-bot/40 hover:bg-white/5"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => {
              const isUser = m.role === "user";
              const last = i === messages.length - 1;
              return (
                <div key={i} className="space-y-2">
                  <div className={clsx("flex items-end gap-2", isUser ? "justify-end" : "justify-start")}>
                    {!isUser && (
                      <span className="mb-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-bot/20 text-bot">
                        <Bot size={12} />
                      </span>
                    )}
                    <div className={clsx("group min-w-0", isUser ? "max-w-[88%]" : "max-w-[92%]")}>
                      <div
                        className={clsx(
                          "rounded-2xl px-3.5 py-2.5 text-xs text-slate-200",
                          isUser
                            ? "rounded-br-sm bg-bot/20 text-slate-100"
                            : "rounded-bl-sm border border-border bg-base text-slate-300"
                        )}
                      >
                        {isUser ? <span className="whitespace-pre-wrap">{m.content}</span> : <ChatMarkdown text={m.content} />}
                      </div>
                      <div className={clsx("mt-0.5 flex items-center gap-2 text-[10px] text-slate-600", isUser && "justify-end")}>
                        <span>{clock(m.at)}</span>
                        {!isUser && factsLine(m.facts) && <span title="What the answer was read from">{factsLine(m.facts)}</span>}
                        {!isUser && (
                          <button
                            type="button"
                            onClick={() => copy(i, m.content)}
                            className="inline-flex items-center gap-1 rounded px-1 text-slate-600 opacity-0 transition hover:text-slate-300 focus-visible:opacity-100 group-hover:opacity-100"
                            aria-label="Copy answer"
                            title="Copy answer"
                          >
                            {copied === i ? <Check size={11} /> : <Copy size={11} />}
                            {copied === i ? "copied" : "copy"}
                          </button>
                        )}
                      </div>
                    </div>
                    {isUser && (
                      <span className="mb-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/10 text-slate-300">
                        <User size={12} />
                      </span>
                    )}
                  </div>
                  {m.proposals?.map((p, j) => (
                    <div key={j} className="pl-8">
                      <ChatProposalCard proposal={p} />
                    </div>
                  ))}
                  {!isUser && last && !busy && (
                    <div className="flex flex-wrap gap-1.5 pl-8">
                      {followUps(lastUser?.content ?? "", m.content).map((q) => (
                        <button
                          key={q}
                          type="button"
                          onClick={() => send(q)}
                          className="rounded-full border border-border px-2.5 py-1 text-[11px] text-slate-400 transition hover:border-bot/40 hover:bg-white/5 hover:text-slate-200"
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}

            {busy && (
              <div className="flex items-center gap-2 pl-8 text-xs text-slate-500">
                <Loader2 size={12} className="animate-spin" /> reading the trade record, every bot and this page…
              </div>
            )}

            {error && <div className="rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">{error}</div>}
          </div>

          <div className="shrink-0 border-t border-border p-2.5">
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                rows={wide ? 2 : 1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(draft);
                  }
                }}
                placeholder="Ask (“why did SBIN not reverse at 1 pm?”) or change a setting (“turn on VWAP on bot 2”)"
                className="max-h-28 flex-1 resize-none rounded-md border border-border bg-base px-2.5 py-2 text-xs text-slate-200 focus:border-bot/60 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => send(draft)}
                disabled={busy || !draft.trim()}
                aria-label="Send"
                className="shrink-0 rounded-md bg-bot/20 p-2 text-bot transition hover:bg-bot/30 disabled:opacity-40"
              >
                <Send size={14} />
              </button>
            </div>
            <p className="mt-1.5 text-[10px] text-slate-600">
              Enter sends, Shift+Enter for a new line. Settings changes show as cards; nothing saves until you tap Apply. Never
              places orders.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
