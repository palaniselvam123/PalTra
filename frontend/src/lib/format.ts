const INR = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const INT = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function money(value: number | null | undefined, withSign = false): string {
  if (value === null || value === undefined) return "—";
  const sign = withSign && value > 0 ? "+" : "";
  return `${sign}${value < 0 ? "-" : ""}₹${INR.format(Math.abs(value))}`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return digits === 0 ? INT.format(value) : value.toFixed(digits);
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)}%`;
}

/** Holding time reads better as "1m 12s" than "72". */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds <= 0) return "under 1s";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

const IST = "Asia/Kolkata";

const IST_TIME: Intl.DateTimeFormatOptions = {
  timeZone: IST,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
};

const IST_DATE: Intl.DateTimeFormatOptions = {
  timeZone: IST,
  day: "2-digit",
  month: "short",
};

const IST_STAMP: Intl.DateTimeFormatOptions = { ...IST_DATE, ...IST_TIME };

const IST_DATETIME: Intl.DateTimeFormatOptions = {
  timeZone: IST,
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
};

function hasZone(value: string): boolean {
  return /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value.trim());
}

/** An instant from a backend clock.

Naive strings are IST wall time, which is how the terminal stores a fill.
Pass `utc` when the field is naive UTC (`datetime.utcnow` on the desk).
A value that already carries Z or an offset is that absolute instant.
*/
export function parseClock(value: string, utc = false): Date | null {
  const text = value.trim().replace(" ", "T");
  if (!text) return null;
  const iso = hasZone(text) ? text : utc ? `${text}Z` : `${text}+05:30`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function istTime(value: string | null | undefined, utc = false): string {
  if (!value) return "—";
  const date = parseClock(value, utc);
  if (!date) return "—";
  return date.toLocaleTimeString("en-IN", IST_TIME);
}

export function istDate(value: string | null | undefined, utc = false): string {
  if (!value) return "—";
  const date = parseClock(value, utc);
  if (!date) return "—";
  return date.toLocaleDateString("en-IN", IST_DATE);
}

export function istDateTime(value: string | null | undefined, utc = false): string {
  if (!value) return "—";
  const date = parseClock(value, utc);
  if (!date) return "—";
  return date.toLocaleString("en-IN", IST_DATETIME);
}

export function istStamp(value: string | null | undefined, utc = false): string {
  if (!value) return "—";
  const date = parseClock(value, utc);
  if (!date) return "—";
  return date.toLocaleString("en-IN", IST_STAMP);
}

export function istNow(): string {
  return new Date().toLocaleTimeString("en-IN", IST_TIME);
}

/** Calendar day in IST, as YYYY-MM-DD. `offsetDays` walks back from today. */
export function istDay(offsetDays = 0): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: IST,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(Date.now() - offsetDays * 86_400_000));
}

/** Backend timestamps are shown in IST even when this browser is not. */
export function timestamp(value: string | null | undefined): string {
  return istStamp(value);
}

export function timeOnly(value: string | null | undefined): string {
  return istTime(value);
}

/** Clock text with an IST label. A missing time stays a dash. */
export function markIst(value: string): string {
  if (!value || value === "—") return "—";
  return value.endsWith(" IST") ? value : `${value} IST`;
}

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-slate-400";
  return value > 0 ? "text-profit" : "text-loss";
}
