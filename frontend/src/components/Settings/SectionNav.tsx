"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

/**
 * The Settings page's section links, pinned under the app header while the
 * long page scrolls, with the section on screen highlighted.
 */
export function SectionNav({ sections }: { sections: [id: string, label: string][] }) {
  const [active, setActive] = useState<string>(sections[0]?.[0] ?? "");
  const [top, setTop] = useState(0);
  // A clicked link stays lit while the page scrolls to it.
  const clickedUntil = useRef(0);

  // Sit just under the sticky header, whatever its height (the simulated-prices banner changes it).
  useEffect(() => {
    const header = document.querySelector("header");
    if (!header) return;
    const measure = () => setTop(header.getBoundingClientRect().height);
    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(header);
    return () => watcher.disconnect();
  }, []);

  // The highlighted link follows the section at the top of the screen.
  useEffect(() => {
    const els = sections.map(([id]) => document.getElementById(id)).filter((el): el is HTMLElement => el != null);
    if (!els.length) return;
    const pick = () => {
      if (Date.now() < clickedUntil.current) return;
      const line = top + 72;
      let current = els[0].id;
      let bestTop = -Infinity;
      for (const el of els) {
        const t = el.getBoundingClientRect().top;
        // Side-by-side sections share a top edge: the first of them counts.
        if (t <= line && t > bestTop + 4) {
          current = el.id;
          bestTop = t;
        }
      }
      // At the very bottom the last section counts, even if it is short.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) current = els[els.length - 1].id;
      setActive(current);
    };
    pick();
    window.addEventListener("scroll", pick, { passive: true });
    window.addEventListener("resize", pick);
    return () => {
      window.removeEventListener("scroll", pick);
      window.removeEventListener("resize", pick);
    };
  }, [sections, top]);

  return (
    <nav
      aria-label="Settings sections"
      style={{ top }}
      className="sticky z-10 -mx-4 flex gap-1.5 overflow-x-auto border-b border-border bg-base/90 px-4 py-2 text-xs backdrop-blur lg:-mx-6 lg:px-6"
    >
      {sections.map(([id, label]) => (
        <a
          key={id}
          href={`#${id}`}
          aria-current={active === id ? "location" : undefined}
          onClick={(e) => {
            // Land the section just below the header and this bar, not under them.
            const el = document.getElementById(id);
            const bar = e.currentTarget.parentElement;
            if (!el || !bar) return;
            e.preventDefault();
            setActive(id);
            clickedUntil.current = Date.now() + 1000;
            const y = el.getBoundingClientRect().top + window.scrollY - top - bar.offsetHeight - 8;
            window.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
            history.replaceState(null, "", `#${id}`);
          }}
          className={clsx(
            "shrink-0 rounded-full border px-3 py-1.5 transition-colors",
            active === id
              ? "border-sky-500 bg-sky-500 font-semibold text-white"
              : "border-border text-slate-300 hover:bg-white/[0.05] hover:text-slate-100"
          )}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
