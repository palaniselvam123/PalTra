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
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/** Backend timestamps are ISO with an offset already applied (IST). */
export function timestamp(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function timeOnly(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-slate-400";
  return value > 0 ? "text-profit" : "text-loss";
}
