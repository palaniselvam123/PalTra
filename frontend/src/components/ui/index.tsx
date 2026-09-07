"use client";

/**
 * Shared UI primitives.
 *
 * The app previously built every panel, stat and badge inline, which is why
 * padding, radius and label styling drifted page to page. These are the
 * building blocks; they encode the decisions once:
 *
 *  - one card radius and border treatment
 *  - one label style (caption, uppercase, muted) so every panel reads the same
 *  - one place where a number becomes "the big number"
 *  - semantic colour only: green and red mean profit and loss, never decoration
 */

import clsx from "clsx";
import type { ReactNode } from "react";

/* ---------------------------------------------------------------- Card ---- */

export function Card({
  children,
  className,
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <div
      className={clsx(
        "rounded-card border border-border bg-surface",
        padded && "p-4",
        className
      )}
    >
      {children}
    </div>
  );
}

/**
 * Card header with a consistent title/description/action layout. Section
 * headings were previously ad-hoc `<h2>`s at four different sizes.
 */
export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        <h2 className="text-section text-slate-200">{title}</h2>
        {description && (
          <p className="mt-0.5 text-caption text-slate-500 tracking-normal">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/** The small uppercase label above a value. One definition, used everywhere. */
export function Label({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={clsx("text-caption text-slate-400", className)}>{children}</span>
  );
}

/* ----------------------------------------------------------- StatTile ---- */

export type StatTone = "neutral" | "profit" | "loss" | "auto";

function toneClass(tone: StatTone, value?: number) {
  if (tone === "profit") return "text-profit";
  if (tone === "loss") return "text-loss";
  if (tone === "auto" && typeof value === "number") {
    if (value > 0) return "text-profit";
    if (value < 0) return "text-loss";
  }
  return "text-slate-100";
}

/**
 * The headline-number pattern: quiet label, loud value, quiet context.
 *
 * The previous cards rendered label and value within ~1px of each other, so
 * nothing was the point of the card. Here the value is the only loud thing.
 */
export function StatTile({
  label,
  value,
  sub,
  tone = "neutral",
  numericValue,
  size = "md",
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: StatTone;
  /** Drives colour when tone is "auto". */
  numericValue?: number;
  size?: "md" | "lg";
  className?: string;
}) {
  return (
    <div className={clsx("min-w-0", className)}>
      <Label>{label}</Label>
      <div
        className={clsx(
          "mt-1 font-mono tabular-nums truncate",
          size === "lg" ? "text-display" : "text-metric",
          toneClass(tone, numericValue)
        )}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-caption tracking-normal text-slate-500">{sub}</div>}
    </div>
  );
}

/** StatTile wrapped in its own card — the dashboard metric-row pattern. */
export function StatCard(props: Parameters<typeof StatTile>[0]) {
  return (
    <Card className="px-4 py-3.5">
      <StatTile {...props} />
    </Card>
  );
}

/* --------------------------------------------------------------- Badge ---- */

export type BadgeTone = "neutral" | "profit" | "loss" | "warn" | "info";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "border-border bg-white/[0.03] text-slate-400",
  profit: "border-profit/30 bg-profit/10 text-profit",
  loss: "border-loss/30 bg-loss/10 text-loss",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-400",
  info: "border-bot/30 bg-bot/10 text-bot",
};

export function Badge({
  children,
  tone = "neutral",
  icon,
  className,
  title,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  icon?: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-caption font-medium",
        BADGE_TONES[tone],
        className
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/** A status dot — carries state with far less visual weight than a full pill. */
export function StatusDot({ tone = "neutral", pulse }: { tone?: BadgeTone; pulse?: boolean }) {
  const color =
    tone === "profit"
      ? "bg-profit"
      : tone === "loss"
        ? "bg-loss"
        : tone === "warn"
          ? "bg-amber-400"
          : tone === "info"
            ? "bg-bot"
            : "bg-slate-500";
  return (
    <span className="relative flex h-1.5 w-1.5 shrink-0">
      {pulse && (
        <span className={clsx("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", color)} />
      )}
      <span className={clsx("relative inline-flex h-1.5 w-1.5 rounded-full", color)} />
    </span>
  );
}

/* -------------------------------------------------------------- Button ---- */

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-bot text-white hover:bg-bot/85 border-transparent",
  secondary: "bg-white/[0.04] text-slate-200 hover:bg-white/[0.08] border-border",
  ghost: "bg-transparent text-slate-400 hover:text-slate-100 hover:bg-white/[0.05] border-transparent",
  danger: "bg-loss text-white hover:bg-loss/85 border-transparent",
};

export function Button({
  children,
  variant = "secondary",
  size = "md",
  icon,
  className,
  ...rest
}: {
  children?: ReactNode;
  variant?: ButtonVariant;
  size?: "sm" | "md";
  icon?: ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "px-2 py-1 text-caption" : "px-3 py-1.5 text-body",
        BUTTON_VARIANTS[variant],
        className
      )}
    >
      {icon}
      {children}
    </button>
  );
}

/* ---------------------------------------------------------- EmptyState ---- */

/**
 * "Waiting for events…" as bare centered text reads like a bug. An empty state
 * should say what will fill it and, where useful, what to do next.
 */
export function EmptyState({
  icon,
  title,
  hint,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex flex-col items-center justify-center px-6 py-10 text-center", className)}>
      {icon && <div className="mb-2.5 text-slate-600">{icon}</div>}
      <p className="text-body font-medium text-slate-300">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-caption tracking-normal leading-relaxed text-slate-500">{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
