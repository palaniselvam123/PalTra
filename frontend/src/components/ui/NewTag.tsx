"use client";

import clsx from "clsx";
import Link from "next/link";
import { featureById, isNew } from "@/lib/features";
import { useNewDays, useNewFeatures, useToday } from "@/components/WhatsNew/useWhatsNew";

/** "14 Nov" from "2026-11-14". */
export function shortDay(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
}

const CHIP =
  "inline-flex shrink-0 items-center rounded-full bg-[#f59e0b] px-1.5 py-px align-middle text-[10px] font-bold uppercase leading-4 tracking-wide text-[#1a1203]";

/**
 * "New" next to a feature's control while it is recent (lib/features.ts; "New" = added in the last N days, set on the
 * What's new page). It links to the feature on the What's new page; `link={false}` inside a button or summary.
 */
export function NewTag({ id, link = true, className }: { id: string; link?: boolean; className?: string }) {
  const [days] = useNewDays();
  const today = useToday();
  const feature = featureById(id);
  if (!feature || !today || !isNew(feature, days, today)) return null;
  const title = `New: added ${shortDay(feature.added)}. See What's new.`;
  if (!link) {
    return (
      <span className={clsx(CHIP, className)} title={title}>
        New
      </span>
    );
  }
  return (
    <Link href={`/whats-new/#f-${feature.id}`} prefetch={false} className={clsx(CHIP, "hover:brightness-110", className)} title={title}>
      New
    </Link>
  );
}

/** "3 new" on a tab or a group: how many New features point at it (`match` reads their link). Never a link. */
export function NewCountChip({ match, className }: { match: (href: string) => boolean; className?: string }) {
  const count = useNewFeatures().filter((f) => match(f.href)).length;
  if (!count) return null;
  return (
    <span className={clsx(CHIP, className)} title={`${count} new feature${count === 1 ? "" : "s"} here. See What's new.`}>
      {count} new
    </span>
  );
}
