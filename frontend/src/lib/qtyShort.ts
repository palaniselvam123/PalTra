/** A share quantity in Indian short form: 950, 12.4K, 2.15L, 1.3Cr. */
export function qtyShort(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e7) return `${(n / 1e7).toFixed(a >= 1e8 ? 1 : 2)}Cr`;
  if (a >= 1e5) return `${(n / 1e5).toFixed(a >= 1e6 ? 1 : 2)}L`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`;
  return String(Math.round(n));
}
