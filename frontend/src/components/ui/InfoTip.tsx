"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { Info } from "lucide-react";

/**
 * A small ⓘ next to a label: the explanation opens in a popover on hover,
 * keyboard focus or tap, instead of a paragraph under every control.
 * Esc, a click outside or leaving it closes it; a tap or click keeps it open.
 */
export function InfoTip({
  children,
  label = "More information",
  className,
}: {
  children: ReactNode;
  /** What the icon is about, for screen readers ("About the flip strategy"). */
  label?: string;
  className?: string;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; width: number; above: boolean } | null>(null);
  const [theme, setTheme] = useState("");
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const id = useRef(`info-${Math.random().toString(36).slice(2, 9)}`);

  const place = useCallback(() => {
    const b = button.current?.getBoundingClientRect();
    if (!b) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(340, vw - 16);
    const left = Math.min(Math.max(8, b.left + b.width / 2 - 24), vw - width - 8);
    const above = b.bottom + 220 > vh && b.top > 240;
    setPos({ left, top: above ? b.top - 8 : b.bottom + 8, width, above });
  }, []);

  const show = () => {
    if (hideTimer.current) clearTimeout(hideTimer.current);
    setTheme(button.current?.closest(".terminal-dark") ? "terminal-dark" : "");
    place();
    setOpen(true);
  };
  const hideSoon = () => {
    if (pinned) return;
    if (hideTimer.current) clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => setOpen(false), 120);
  };
  const close = () => {
    setOpen(false);
    setPinned(false);
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!button.current?.contains(t) && !pop.current?.contains(t)) close();
    };
    const onMove = () => place();
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open, place]);

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id.current : undefined}
        onMouseEnter={show}
        onMouseLeave={hideSoon}
        onFocus={show}
        onBlur={hideSoon}
        onClick={(e) => {
          // Inside a <label> a click must not tick the checkbox or open the select.
          e.preventDefault();
          e.stopPropagation();
          if (open && pinned) close();
          else {
            show();
            setPinned(true);
          }
        }}
        className={clsx(
          "relative inline-flex h-5 w-5 shrink-0 items-center after:absolute after:-inset-2.5 after:content-[''] justify-center rounded-full align-middle text-slate-400 hover:bg-white/10 hover:text-sky-300 focus-visible:text-sky-300",
          open && "text-sky-300",
          className
        )}
      >
        <Info size={14} aria-hidden />
      </button>
      {open && pos && typeof document !== "undefined"
        ? createPortal(
            <div className={theme}>
              <div
                ref={pop}
                id={id.current}
                role="tooltip"
                onMouseEnter={show}
                onMouseLeave={hideSoon}
                style={{ left: pos.left, top: pos.top, width: pos.width, transform: pos.above ? "translateY(-100%)" : undefined }}
                className="fixed z-[80] rounded-lg border border-border bg-surface p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-slate-200 shadow-xl"
              >
                {children}
              </div>
            </div>,
            document.body
          )
        : null}
    </>
  );
}
