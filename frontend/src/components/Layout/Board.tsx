"use client";

/**
 * Movable, resizable panels for a page.
 *
 * Each panel can be dragged to a new place by its grip (or moved with the
 * arrow buttons, for keyboards and touch screens), made narrower or wider on
 * wide screens (¼ … full of a 12-column grid), made taller or shorter by its
 * bottom edge, and hidden. "Pop out" turns a panel into a floating window
 * that can be dragged anywhere on the screen by its title bar and resized
 * from its corner; "Dock" puts it back. The layout is kept per page in this
 * browser only; "Reset layout" puts the page back as it was built.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { ArrowDown, ArrowUp, EyeOff, GripVertical, MoveHorizontal, PanelBottomClose, PictureInPicture2, RotateCcw } from "lucide-react";

export type PanelSpec = {
  id: string;
  /** Shown on the panel's tools and in the hidden list. */
  title: string;
  node: ReactNode;
  /** Columns out of 12 on a wide screen (default 12). */
  span?: Span;
  /**
   * What the bottom edge resizes: "box" (default) caps the panel's height and
   * scrolls inside it; "var" sets the CSS variable `--panel-h` that the content
   * sizes itself by (the chart); "none" has no handle.
   */
  resize?: "box" | "var" | "none";
  /** Smallest height the edge can be dragged to, in pixels (default 160). */
  minHeight?: number;
  /** Can the panel be hidden (default true). */
  hideable?: boolean;
};

const SPANS = [3, 4, 6, 8, 9, 12] as const;
type Span = (typeof SPANS)[number];
const SPAN_LABEL: Record<Span, string> = { 3: "¼", 4: "⅓", 6: "½", 8: "⅔", 9: "¾", 12: "full" };

/** A popped-out panel's window: left/top/width/height in screen pixels. */
type Rect = { x: number; y: number; w: number; h: number };

type Saved = {
  order: string[];
  span: Record<string, Span>;
  height: Record<string, number>;
  hidden: string[];
  floating: Record<string, Rect>;
};

const EMPTY: Saved = { order: [], span: {}, height: {}, hidden: [], floating: {} };

function validRect(v: unknown): v is Rect {
  const r = v as Rect;
  return !!r && [r.x, r.y, r.w, r.h].every((n) => typeof n === "number" && Number.isFinite(n));
}

/** Keep a window on screen (at least its title bar), whatever the screen size is now. */
function clampRect(r: Rect): Rect {
  if (typeof window === "undefined") return r;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.min(Math.max(280, r.w), vw);
  const h = Math.min(Math.max(160, r.h), vh);
  return { w, h, x: Math.min(Math.max(0, r.x), vw - Math.min(w, 120)), y: Math.min(Math.max(0, r.y), vh - 40) };
}

function defaultRect(index: number): Rect {
  const vw = typeof window === "undefined" ? 1200 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const w = Math.min(960, vw - 32);
  const h = Math.min(560, vh - 120);
  return clampRect({ x: Math.max(16, (vw - w) / 2) + index * 24, y: 80 + index * 24, w, h });
}

function read(key: string): Saved {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return EMPTY;
    const v = JSON.parse(raw) as Partial<Saved>;
    return {
      order: Array.isArray(v.order) ? v.order.filter((x): x is string => typeof x === "string") : [],
      span: v.span && typeof v.span === "object" ? (v.span as Saved["span"]) : {},
      height: v.height && typeof v.height === "object" ? (v.height as Saved["height"]) : {},
      hidden: Array.isArray(v.hidden) ? v.hidden.filter((x): x is string => typeof x === "string") : [],
      floating:
        v.floating && typeof v.floating === "object"
          ? Object.fromEntries(Object.entries(v.floating).filter(([, r]) => validRect(r)))
          : {},
    };
  } catch {
    return EMPTY;
  }
}

function write(key: string, value: Saved) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private window: the layout just is not kept */
  }
}

/**
 * `widths` false: a board inside one column of a page (the dashboard's workspace or rail), where
 * panels stack full width and only move, resize and hide.
 */
export function Board({
  page,
  panels,
  className,
  widths = true,
}: {
  page: string;
  panels: PanelSpec[];
  className?: string;
  widths?: boolean;
}) {
  const key = `layout.${page}`;
  const [saved, setSaved] = useState<Saved>(EMPTY);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setSaved(read(key));
    setReady(true);
  }, [key]);
  const update = useCallback(
    (fn: (prev: Saved) => Saved) =>
      setSaved((prev) => {
        const next = fn(prev);
        write(key, next);
        return next;
      }),
    [key]
  );

  const ids = panels.map((p) => p.id);
  // Saved order first (panels that still exist), then any new panels in their built order.
  const order = useMemo(() => {
    const known = saved.order.filter((id) => ids.includes(id));
    return [...known, ...ids.filter((id) => !known.includes(id))];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved.order, ids.join("|")]);
  const byId = new Map(panels.map((p) => [p.id, p]));
  const hidden = saved.hidden.filter((id) => ids.includes(id));
  const floating = order.filter((id) => !hidden.includes(id) && saved.floating?.[id] != null);
  const shown = order.filter((id) => !hidden.includes(id) && !floating.includes(id));
  const customised =
    saved.order.length > 0 ||
    Object.keys(saved.span).length > 0 ||
    Object.keys(saved.height).length > 0 ||
    hidden.length > 0 ||
    floating.length > 0;
  // A popped-out window keeps the page's theme rules (the terminal scopes its light theme).
  const rootRef = useRef<HTMLDivElement>(null);
  const [themeClass, setThemeClass] = useState("");
  useEffect(() => {
    setThemeClass(rootRef.current?.closest(".terminal-dark") ? "terminal-dark" : "");
  }, []);
  // The window clicked last comes to the front.
  const [front, setFront] = useState<string | null>(null);
  const popOut = (id: string) =>
    update((prev) => ({ ...prev, floating: { ...(prev.floating ?? {}), [id]: defaultRect(Object.keys(prev.floating ?? {}).length) } }));
  const dock = (id: string) =>
    update((prev) => {
      const next = { ...(prev.floating ?? {}) };
      delete next[id];
      return { ...prev, floating: next };
    });
  const moveWindow = (id: string, r: Rect) => update((prev) => ({ ...prev, floating: { ...(prev.floating ?? {}), [id]: r } }));

  /** Put `id` just before or after `target` (both by id). */
  const place = (id: string, target: string, after: boolean) =>
    update((prev) => {
      const list = order.filter((x) => x !== id);
      const at = list.indexOf(target) + (after ? 1 : 0);
      return { ...prev, order: [...list.slice(0, at), id, ...list.slice(at)] };
    });
  // One place up or down among the panels on screen.
  const step = (id: string, dir: -1 | 1) => {
    const target = shown[shown.indexOf(id) + dir];
    if (target != null) place(id, target, dir > 0);
  };

  // Drag and drop by the grip.
  const dragId = useRef<string | null>(null);
  const [dropAt, setDropAt] = useState<{ id: string; after: boolean } | null>(null);
  const onDragOver = (e: DragEvent<HTMLElement>, id: string) => {
    if (!dragId.current || dragId.current === id) return;
    e.preventDefault();
    const box = e.currentTarget.getBoundingClientRect();
    // Side by side panels compare across; stacked ones up and down.
    const wide = box.width < (e.currentTarget.parentElement?.clientWidth ?? box.width) * 0.9;
    const after = wide ? e.clientX > box.left + box.width / 2 : e.clientY > box.top + box.height / 2;
    setDropAt((cur) => (cur?.id === id && cur.after === after ? cur : { id, after }));
  };
  const onDrop = (e: DragEvent<HTMLElement>) => {
    e.preventDefault();
    const id = dragId.current;
    if (id && dropAt && dropAt.id !== id) place(id, dropAt.id, dropAt.after);
    dragId.current = null;
    setDropAt(null);
  };

  return (
    <div ref={rootRef} className={clsx("space-y-2", className)}>
      {floating.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          <span>Popped out:</span>
          {floating.map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => dock(id)}
              className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-slate-300 ring-1 ring-inset ring-sky-400/40 hover:bg-white/5"
              title="Put this panel back in the page"
            >
              {byId.get(id)?.title ?? id} <PanelBottomClose size={11} aria-hidden />
            </button>
          ))}
        </div>
      ) : null}
      {ready && typeof document !== "undefined"
        ? floating.map((id) =>
            createPortal(
              <FloatingPanel
                key={id}
                spec={byId.get(id)!}
                rect={clampRect(saved.floating[id])}
                themeClass={themeClass}
                onTop={front === id}
                onFocus={() => setFront(id)}
                onMove={(r) => moveWindow(id, r)}
                onDock={() => dock(id)}
              />,
              document.body,
              id
            )
          )
        : null}
      {hidden.length > 0 || customised ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          {hidden.length > 0 ? (
            <>
              <span>Hidden:</span>
              {hidden.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => update((prev) => ({ ...prev, hidden: prev.hidden.filter((x) => x !== id) }))}
                  className="rounded-full px-2 py-0.5 text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
                  title="Show this panel again"
                >
                  {byId.get(id)?.title ?? id} ＋
                </button>
              ))}
            </>
          ) : null}
          {customised ? (
            <button
              type="button"
              onClick={() => update(() => EMPTY)}
              className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/5"
              title="Put every panel back where and how big it was"
            >
              <RotateCcw size={11} aria-hidden /> Reset layout
            </button>
          ) : null}
        </div>
      ) : null}
      <div className={clsx("grid grid-cols-1 gap-4", widths && "board-grid")}>
        {shown.map((id, index) => {
          const spec = byId.get(id)!;
          const span = saved.span[id] ?? spec.span ?? 12;
          return (
            <BoardPanel
              key={id}
              spec={spec}
              span={span}
              widths={widths}
              height={ready ? saved.height[id] : undefined}
              first={index === 0}
              last={index === shown.length - 1}
              dropBefore={dropAt?.id === id && !dropAt.after}
              dropAfter={dropAt?.id === id && dropAt.after}
              onGrip={(on) => {
                dragId.current = on ? id : null;
              }}
              onDragOver={(e) => onDragOver(e, id)}
              onDrop={onDrop}
              onDragEnd={() => {
                dragId.current = null;
                setDropAt(null);
              }}
              onStep={(dir) => step(id, dir)}
              onSpan={() => {
                const next = SPANS[(SPANS.indexOf(span) + 1) % SPANS.length];
                update((prev) => ({ ...prev, span: { ...prev.span, [id]: next } }));
              }}
              onHeight={(h) =>
                update((prev) => {
                  const height = { ...prev.height };
                  if (h == null) delete height[id];
                  else height[id] = h;
                  return { ...prev, height };
                })
              }
              onHide={() => update((prev) => ({ ...prev, hidden: [...prev.hidden.filter((x) => x !== id), id] }))}
              onPopOut={() => popOut(id)}
            />
          );
        })}
      </div>
    </div>
  );
}

function BoardPanel({
  spec,
  span,
  widths,
  height,
  first,
  last,
  dropBefore,
  dropAfter,
  onGrip,
  onDragOver,
  onDrop,
  onDragEnd,
  onStep,
  onSpan,
  onHeight,
  onHide,
  onPopOut,
}: {
  spec: PanelSpec;
  span: Span;
  widths: boolean;
  height: number | undefined;
  first: boolean;
  last: boolean;
  dropBefore: boolean;
  dropAfter: boolean;
  onGrip: (on: boolean) => void;
  onDragOver: (e: DragEvent<HTMLElement>) => void;
  onDrop: (e: DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onStep: (dir: -1 | 1) => void;
  onSpan: () => void;
  onHeight: (h: number | null) => void;
  onHide: () => void;
  onPopOut: () => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const [draggable, setDraggable] = useState(false);
  const [live, setLive] = useState<number | null>(null);
  const resize = spec.resize ?? "box";
  const min = spec.minHeight ?? 160;
  const shownHeight = live ?? height;

  // Bottom edge: drag to set the height (box) or the content's own height (var).
  const startResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const el = ref.current;
    if (!el) return;
    const content = el.querySelector<HTMLElement>("[data-panel-content]");
    const start = e.clientY;
    const base = shownHeight ?? (resize === "var" ? (content?.querySelector<HTMLElement>("[data-panel-sized]")?.offsetHeight ?? 480) : (content?.offsetHeight ?? 400));
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    let last = base;
    const onMove = (ev: PointerEvent) => {
      last = Math.max(min, Math.round(base + ev.clientY - start));
      setLive(last);
    };
    const onUp = () => {
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
      setLive(null);
      onHeight(last);
    };
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  };

  const style: CSSProperties & Record<string, string | number> = { "--span": span };
  if (resize === "var" && shownHeight != null) style["--panel-h"] = `${shownHeight}px`;
  const tool = "inline-flex h-7 min-w-7 items-center justify-center rounded-md text-slate-400 hover:bg-white/10 hover:text-slate-100 disabled:opacity-30";

  return (
    <section
      ref={ref}
      aria-label={spec.title}
      data-panel={spec.id}
      data-sized={resize === "var" && shownHeight != null ? "" : undefined}
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", spec.id);
      }}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={() => {
        setDraggable(false);
        onGrip(false);
        onDragEnd();
      }}
      style={style}
      className={clsx(
        "board-panel group/panel relative min-w-0",
        dropBefore && "before:absolute before:-top-2.5 before:left-0 before:right-0 before:h-1 before:rounded before:bg-sky-400",
        dropAfter && "after:absolute after:-bottom-2.5 after:left-0 after:right-0 after:h-1 after:rounded after:bg-sky-400"
      )}
    >
      {/* Panel tools: on hover or keyboard focus, so they never cover the content otherwise. */}
      <div
        role="toolbar"
        aria-label={`${spec.title} layout`}
        className="pointer-events-none absolute -top-3 right-2 z-30 flex items-center gap-0.5 rounded-lg border border-white/10 bg-[#0f131a]/95 p-0.5 opacity-0 shadow-lg transition-opacity focus-within:pointer-events-auto focus-within:opacity-100 group-hover/panel:pointer-events-auto group-hover/panel:opacity-100"
      >
        <span className="px-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{spec.title}</span>
        <button
          type="button"
          aria-label={`Drag to move ${spec.title}`}
          title="Drag to move"
          className={clsx(tool, "cursor-grab active:cursor-grabbing")}
          onPointerDown={() => {
            setDraggable(true);
            onGrip(true);
          }}
          onPointerUp={() => {
            setDraggable(false);
            onGrip(false);
          }}
        >
          <GripVertical size={14} aria-hidden />
        </button>
        <button type="button" className={tool} disabled={first} onClick={() => onStep(-1)} aria-label={`Move ${spec.title} up`} title="Move up">
          <ArrowUp size={13} aria-hidden />
        </button>
        <button type="button" className={tool} disabled={last} onClick={() => onStep(1)} aria-label={`Move ${spec.title} down`} title="Move down">
          <ArrowDown size={13} aria-hidden />
        </button>
        <button
          type="button"
          className={clsx(tool, "hidden gap-1 px-1.5 text-[11px]", widths && "xl:inline-flex")}
          onClick={onSpan}
          aria-label={`Width of ${spec.title}: ${SPAN_LABEL[span]}. Change`}
          title="Change the width (wide screens)"
        >
          <MoveHorizontal size={13} aria-hidden /> {SPAN_LABEL[span]}
        </button>
        {resize !== "none" && height != null ? (
          <button type="button" className={clsx(tool, "px-1.5 text-[11px]")} onClick={() => onHeight(null)} title="Back to the normal height">
            Auto height
          </button>
        ) : null}
        <button
          type="button"
          className={clsx(tool, "hidden md:inline-flex")}
          onClick={onPopOut}
          aria-label={`Pop out ${spec.title}`}
          title="Pop out: a floating window you can drag anywhere"
        >
          <PictureInPicture2 size={13} aria-hidden />
        </button>
        {spec.hideable !== false ? (
          <button type="button" className={tool} onClick={onHide} aria-label={`Hide ${spec.title}`} title="Hide (show it again from the Hidden list)">
            <EyeOff size={13} aria-hidden />
          </button>
        ) : null}
      </div>
      <div
        data-panel-content
        className={clsx(resize === "box" && shownHeight != null && "overflow-auto rounded-xl")}
        style={resize === "box" && shownHeight != null ? { height: shownHeight } : undefined}
      >
        {spec.node}
      </div>
      {resize !== "none" ? (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label={`Resize ${spec.title}`}
          title="Drag to resize"
          onPointerDown={startResize}
          onDoubleClick={() => onHeight(null)}
          className="absolute -bottom-2 left-1/2 z-20 hidden h-3 w-24 -translate-x-1/2 cursor-ns-resize touch-none items-center justify-center opacity-0 transition-opacity group-hover/panel:opacity-100 md:flex"
        >
          <span className="h-1 w-12 rounded-full bg-slate-500/70" />
        </div>
      ) : null}
    </section>
  );
}

/**
 * A popped-out panel: a window fixed on the screen, dragged by its title bar
 * and resized from its bottom-right corner. Its place and size are saved.
 */
function FloatingPanel({
  spec,
  rect,
  themeClass,
  onTop,
  onFocus,
  onMove,
  onDock,
}: {
  spec: PanelSpec;
  rect: Rect;
  themeClass: string;
  onTop: boolean;
  onFocus: () => void;
  onMove: (r: Rect) => void;
  onDock: () => void;
}) {
  const [live, setLive] = useState<Rect | null>(null);
  const r = live ?? rect;

  // One pointer gesture: move the window (title bar) or resize it (corner).
  const gesture = (e: ReactPointerEvent<HTMLElement>, kind: "move" | "size") => {
    if (e.button !== 0) return;
    if (kind === "move" && (e.target as HTMLElement).closest("button")) return;
    e.preventDefault();
    onFocus();
    const start = { x: e.clientX, y: e.clientY };
    const base = r;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    let last = base;
    const onPointerMove = (ev: PointerEvent) => {
      const dx = ev.clientX - start.x;
      const dy = ev.clientY - start.y;
      last = clampRect(kind === "move" ? { ...base, x: base.x + dx, y: base.y + dy } : { ...base, w: base.w + dx, h: base.h + dy });
      setLive(last);
    };
    const onUp = () => {
      target.removeEventListener("pointermove", onPointerMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
      setLive(null);
      onMove(last);
    };
    target.addEventListener("pointermove", onPointerMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  };

  // Keep it on screen when the window shrinks.
  useEffect(() => {
    const onResize = () => {
      const next = clampRect(rect);
      if (next.x !== rect.x || next.y !== rect.y || next.w !== rect.w || next.h !== rect.h) onMove(next);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [rect, onMove]);

  return (
    <div className={themeClass}>
      <section
        aria-label={`${spec.title} (floating window)`}
        onPointerDown={onFocus}
        style={{ left: r.x, top: r.y, width: r.w, height: r.h }}
        className={clsx(
          "fixed flex flex-col overflow-hidden rounded-xl border border-sky-400/40 bg-base shadow-2xl",
          onTop ? "z-[60]" : "z-[55]"
        )}
      >
        <header
          onPointerDown={(e) => gesture(e, "move")}
          onDoubleClick={onDock}
          title="Drag to move · double-click to dock"
          className="flex h-9 shrink-0 cursor-move touch-none select-none items-center gap-2 border-b border-white/10 bg-surface2 px-3"
        >
          <GripVertical size={14} aria-hidden className="text-slate-500" />
          <span className="truncate text-xs font-semibold uppercase tracking-wider text-slate-300">{spec.title}</span>
          <button
            type="button"
            onClick={onDock}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-slate-300 ring-1 ring-inset ring-white/15 hover:bg-white/10"
            title="Put it back in the page"
          >
            <PanelBottomClose size={12} aria-hidden /> Dock
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-2">{spec.node}</div>
        <div
          role="separator"
          aria-label={`Resize ${spec.title}`}
          title="Drag to resize"
          onPointerDown={(e) => gesture(e, "size")}
          className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize touch-none"
        >
          <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4 text-slate-500">
            <path d="M15 6 6 15M15 10l-5 5M15 14l-1 1" stroke="currentColor" strokeWidth="1.5" fill="none" />
          </svg>
        </div>
      </section>
    </div>
  );
}
