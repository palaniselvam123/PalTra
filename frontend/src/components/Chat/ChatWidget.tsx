"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { Bot, Eraser, Loader2, MessageSquare, Send, X } from "lucide-react";
import { api, type ChatTurn } from "@/lib/api";

/** Floating "ask the bot" panel, mounted in the root layout so it follows the
 *  user across Dashboard, Reports and Settings — the questions it answers
 *  ("why did this lose money?") get asked from all three. */
export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<{ configured: boolean; model: string; suggestions: string[] } | null>(null);
  const [messages, setMessages] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && !status) api.getChatStatus().then(setStatus).catch(() => {});
  }, [open, status]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;

    // Snapshot the history BEFORE appending: the backend wants prior turns,
    // and the new question is sent separately as `message`.
    const priorTurns = messages;
    setMessages([...priorTurns, { role: "user", content: question }]);
    setDraft("");
    setBusy(true);
    setError(null);

    try {
      const res = await api.askChat(question, priorTurns);
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply }]);
    } catch (e: any) {
      setError(e.message ?? "Could not reach the assistant");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        aria-label="Ask the bot about its trades"
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 px-4 py-3 rounded-full bg-bot text-slate-900 font-semibold text-sm shadow-lg shadow-bot/20 hover:brightness-110 transition"
      >
        <MessageSquare size={16} />
        Ask the bot
      </button>
    );
  }

  return (
    <div className="fixed bottom-5 right-5 z-40 w-[min(420px,calc(100vw-2.5rem))] max-h-[min(640px,calc(100vh-3rem))] flex flex-col rounded-xl border border-border bg-surface shadow-2xl overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border shrink-0">
        <Bot size={15} className="text-bot" />
        <span className="text-sm font-medium text-slate-200">Ask the bot</span>
        {messages.length > 0 && (
          <button
            onClick={() => {
              setMessages([]);
              setError(null);
            }}
            title="Clear conversation"
            className="ml-auto text-slate-500 hover:text-slate-300 transition"
          >
            <Eraser size={14} />
          </button>
        )}
        <button
          onClick={() => setOpen(false)}
          aria-label="Close"
          className={clsx("text-slate-500 hover:text-slate-300 transition", messages.length === 0 && "ml-auto")}
        >
          <X size={16} />
        </button>
      </div>

      {status && !status.configured ? (
        <div className="px-4 py-6 text-center space-y-2">
          <p className="text-xs text-slate-500">
            This assistant explains your bot&apos;s own trades — why it entered, why it exited, and where the P&amp;L
            went. It needs the same OpenAI key as the AI expert.
          </p>
          <Link
            href="/settings"
            onClick={() => setOpen(false)}
            className="inline-block px-3 py-1.5 rounded-md bg-bot/20 text-bot text-xs font-medium hover:bg-bot/30 transition"
          >
            Add a key in Settings
          </Link>
        </div>
      ) : (
        <>
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-[180px]">
            {messages.length === 0 && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500">
                  I answer from your actual trade record — the fills, the charges, the opening ranges I measured and
                  my own console log. If something isn&apos;t recorded, I&apos;ll say so rather than guess.
                </p>
                <div className="space-y-1.5">
                  {(status?.suggestions ?? []).map((q) => (
                    <button
                      key={q}
                      onClick={() => send(q)}
                      className="w-full text-left text-xs px-2.5 py-1.5 rounded-md border border-border text-slate-300 hover:bg-white/5 hover:border-bot/40 transition"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={clsx("flex", m.role === "user" ? "justify-end" : "justify-start")}>
                <div
                  className={clsx(
                    "rounded-lg px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap max-w-[92%]",
                    m.role === "user"
                      ? "bg-bot/20 text-slate-100"
                      : "bg-base border border-border text-slate-300"
                  )}
                >
                  {m.content}
                </div>
              </div>
            ))}

            {busy && (
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                <Loader2 size={12} className="animate-spin" /> reading the trade record…
              </div>
            )}

            {error && <div className="text-xs text-loss">{error}</div>}
          </div>

          <div className="border-t border-border p-2.5 shrink-0">
            <div className="flex items-end gap-2">
              <textarea
                rows={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(draft);
                  }
                }}
                placeholder="Why did you buy that stock?"
                className="flex-1 resize-none bg-base border border-border rounded-md px-2.5 py-2 text-xs text-slate-200 focus:outline-none focus:border-bot/60 max-h-24"
              />
              <button
                onClick={() => send(draft)}
                disabled={busy || !draft.trim()}
                aria-label="Send"
                className="p-2 rounded-md bg-bot/20 text-bot hover:bg-bot/30 transition disabled:opacity-40 shrink-0"
              >
                <Send size={14} />
              </button>
            </div>
            <p className="text-[10px] text-slate-600 mt-1.5">
              Paper trading only. Explains past decisions — not advice on what to trade next.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
