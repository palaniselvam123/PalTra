"use client";

import { ExternalLink } from "lucide-react";
import clsx from "clsx";
import { resolveApiBase } from "@/lib/api";

/** Groww's page for a stock, through the desk's lookup (the page address is a name slug, not the symbol). */
export function growwHref(symbol: string): string {
  return `${resolveApiBase()}/api/instruments/groww/${encodeURIComponent(symbol.toUpperCase())}`;
}

/** The stock's name as a link that opens it on groww.in in a new tab (your Groww login there is used). */
export function GrowwLink({ symbol, className }: { symbol: string; className?: string }) {
  return (
    <a
      href={growwHref(symbol)}
      target="_blank"
      rel="noopener noreferrer"
      title={`Open ${symbol} on Groww (new tab)`}
      onClick={(e) => e.stopPropagation()}
      className={clsx("group inline-flex items-center gap-1 hover:text-accentSky hover:underline", className)}
    >
      {symbol}
      <ExternalLink size={11} aria-hidden className="opacity-40 group-hover:opacity-100" />
    </a>
  );
}
