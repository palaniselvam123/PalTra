/** "Last scan 14:32 IST · 5 min ago" for the Scalp page's scan cards. */
export function lastScanText(
  data: { finished_at?: string | null; as_of: string | null; minutes: number; market_open: boolean } | null,
  shownMinutes: number,
  now: number = Date.now()
): string | null {
  const when = data?.finished_at ?? data?.as_of;
  if (!data || !when) return null;
  const at = new Date(when);
  if (Number.isNaN(at.getTime())) return null;
  const time = at.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  const mins = Math.max(0, Math.round((now - at.getTime()) / 60000));
  const ago = mins === 0 ? "just now" : mins < 60 ? `${mins} min ago` : `${Math.floor(mins / 60)} h ${mins % 60} min ago`;
  const sameDay =
    at.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" }) === new Date(now).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  const day = sameDay ? "" : ` on ${at.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" })}`;
  const size = data.minutes === shownMinutes ? "" : ` · on the ${data.minutes}-min candle`;
  return `Last scan ${time} IST${day} · ${ago}${size}${data.market_open ? "" : " · market closed"}`;
}
