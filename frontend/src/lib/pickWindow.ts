/** The time window a Scalp pick backtest runs in (backend trend_picks.parse_window): pick from 09:15 to 15:25 IST,
 * trade until 09:20 to 15:30 IST. Quarter-hour steps for the menus. */
function quarterHours(from: string, to: string): string[] {
  const out: string[] = [];
  const [fh, fm] = from.split(":").map(Number);
  const [th, tm] = to.split(":").map(Number);
  for (let m = fh * 60 + fm; m <= th * 60 + tm; m += 15) {
    out.push(`${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`);
  }
  return out;
}

/** When the stocks are picked (and trading starts). */
export const PICK_TIMES = quarterHours("09:15", "15:15");
/** When trading stops each day; 15:30 is the usual close. */
export const UNTIL_TIMES = quarterHours("09:30", "15:30");
export const DAY_CLOSE = "15:30";

/** The stop times that come after a pick time. */
export function untilAfter(pick: string): string[] {
  return UNTIL_TIMES.filter((t) => t > pick);
}

/** A stop time kept valid when the pick time moves past it. */
export function fitUntil(until: string, pick: string): string {
  return until > pick ? until : DAY_CLOSE;
}
