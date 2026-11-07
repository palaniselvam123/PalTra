"use client";

import { type ReactNode } from "react";
import clsx from "clsx";
import { ChevronDown } from "lucide-react";

/**
 * One card on the Settings page, the same shape everywhere so a beginner always knows where to look:
 * a title with an On / Off chip, one plain-words line that says what the card does, "How it works"
 * (the long explanation, folded away until asked for) and then the controls. Display only.
 */
export type CardStatus = { on: boolean; text?: string } | null | undefined;

export function StatusChip({ status }: { status: CardStatus }) {
  if (!status) return null;
  return (
    <span
      className={clsx(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset",
        status.on ? "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30" : "bg-white/5 text-slate-400 ring-white/15"
      )}
    >
      <span aria-hidden className={clsx("h-1.5 w-1.5 rounded-full", status.on ? "bg-emerald-400" : "bg-slate-500")} />
      {status.text ?? (status.on ? "On" : "Off")}
    </span>
  );
}

/** A small tag on a card or a group: "Start here" for the few settings a beginner needs, "Advanced" for the rest. */
export function TierTag({ tier }: { tier?: "start" | "advanced" | null }) {
  if (!tier) return null;
  return (
    <span
      className={clsx(
        "inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        tier === "start" ? "bg-sky-500/15 text-sky-300" : "bg-white/5 text-slate-400 ring-1 ring-inset ring-white/10"
      )}
    >
      {tier === "start" ? "Start here" : "Advanced"}
    </span>
  );
}

/** The title row, the plain line and the folded explanation; used by SettingsCard and by cards that keep their own frame. */
export function CardHeader({
  id,
  title,
  icon,
  summary,
  help,
  status,
  badge,
  tier,
}: {
  id: string;
  title: ReactNode;
  icon?: ReactNode;
  /** One or two plain sentences: what this does and whether it trades. Always visible. */
  summary?: ReactNode;
  /** The full explanation, folded under "How it works". */
  help?: ReactNode;
  status?: CardStatus;
  /** Extra chips after the title (a bot's mode, a count). */
  badge?: ReactNode;
  tier?: "start" | "advanced" | null;
}) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h2 id={`${id}-title`} className="flex items-center gap-2 text-base font-semibold text-slate-100">
          {icon ? <span className="text-sky-300">{icon}</span> : null}
          {title}
        </h2>
        <StatusChip status={status} />
        <TierTag tier={tier} />
        {badge}
      </div>
      {summary ? <p className="mt-1 max-w-3xl text-sm leading-snug text-slate-300">{summary}</p> : null}
      {help ? (
        <details className="group/help mt-1.5 max-w-3xl">
          <summary className="inline-flex min-h-8 cursor-pointer list-none items-center gap-1 rounded text-xs font-semibold text-sky-300 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 [&::-webkit-details-marker]:hidden">
            <ChevronDown size={14} aria-hidden className="transition-transform group-[:not([open])]/help:-rotate-90" />
            How it works
          </summary>
          <div className="mt-1 rounded-md border border-white/10 bg-black/20 p-3 text-xs leading-relaxed text-slate-300">{help}</div>
        </details>
      ) : null}
    </div>
  );
}

export function SettingsCard({
  id,
  title,
  icon,
  summary,
  help,
  status,
  badge,
  tier,
  children,
  className,
  "aria-label": ariaLabel,
}: {
  id: string;
  title: ReactNode;
  icon?: ReactNode;
  summary?: ReactNode;
  help?: ReactNode;
  status?: CardStatus;
  badge?: ReactNode;
  tier?: "start" | "advanced" | null;
  children?: ReactNode;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      aria-label={ariaLabel}
      className={clsx("terminal-dark scroll-mt-24 rounded-xl border border-white/10 bg-[#151921] p-4 sm:p-5", className)}
    >
      <CardHeader id={id} title={title} icon={icon} summary={summary} help={help} status={status} badge={badge} tier={tier} />
      {children}
    </section>
  );
}
