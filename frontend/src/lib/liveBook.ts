/**
 * A bot's own book of real days on the Compare page (backend/live_book.py): a negative run id that carries what it
 * is, -(first day YYYYMMDD × 10 000 + span × 100 + bot × 10 + book), book 1 = NSE live, 2 = practice, 3 = research.
 * Display only: the same arithmetic as the backend, so a ticked chip can be named before the page loads.
 */
export type LiveBook = { bot: number; book: "LIVE" | "PAPER" | "RESEARCH"; start: string; end: string };

const BOOKS = { 1: "LIVE", 2: "PAPER", 3: "RESEARCH" } as const;
const WORD = { LIVE: "NSE live", PAPER: "practice", RESEARCH: "research desk" } as const;

export const isLiveBook = (id: number) => id < 0;

const iso = (d: Date) => d.toISOString().slice(0, 10);

export function decodeLiveBook(id: number): LiveBook | null {
  let raw = -id;
  if (!Number.isInteger(raw) || raw <= 0) return null;
  const code = raw % 10;
  raw = Math.floor(raw / 10);
  const bot = raw % 10;
  raw = Math.floor(raw / 10);
  const span = raw % 100;
  const day = String(Math.floor(raw / 100));
  const book = BOOKS[code as 1 | 2 | 3];
  if (!book || day.length !== 8) return null;
  const first = new Date(`${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)}T00:00:00Z`);
  if (Number.isNaN(first.getTime())) return null;
  const last = new Date(first.getTime() + span * 86_400_000);
  return { bot, book, start: iso(first), end: iso(last) };
}

const short = (isoDay: string) =>
  new Date(`${isoDay}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });

/** "Bot 1 NSE live · 17 Nov" (the backend's live_book.label). */
export function liveBookLabel(id: number): string {
  const b = decodeLiveBook(id);
  if (!b) return `#${id}`;
  const when = b.start === b.end ? short(b.start) : `${short(b.start)} - ${short(b.end)}`;
  const who = b.book === "RESEARCH" ? "Research desk" : `Bot ${b.bot} ${WORD[b.book]}`;
  return `${who} · ${when}`;
}

/** A run's name anywhere on the Compare page: "#12" for a backtest, the live book's name for a real day. */
export const runName = (id: number) => (isLiveBook(id) ? liveBookLabel(id) : `#${id}`);
