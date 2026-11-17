/** The last `count` weekdays that have closed, oldest first, in IST. A day
 * counts as closed after 15:30 IST. Exchange holidays are not known here; a
 * replay skips a day with no candles. */
export function lastClosedWeekdays(count: number, now: Date = new Date()): string[] {
  const ist = new Date(now.getTime() + 330 * 60_000); // UTC fields now read as IST
  const day = new Date(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate()));
  const closedToday = ist.getUTCHours() * 60 + ist.getUTCMinutes() >= 15 * 60 + 30;
  if (!closedToday) day.setUTCDate(day.getUTCDate() - 1);
  const out: string[] = [];
  while (out.length < count) {
    const wd = day.getUTCDay();
    if (wd !== 0 && wd !== 6) out.unshift(day.toISOString().slice(0, 10));
    day.setUTCDate(day.getUTCDate() - 1);
  }
  return out;
}

/** Today in IST (YYYY-MM-DD) while its session is on and a replay of it can start (09:20 to 15:30 IST, Mon-Fri),
 * else null. After 15:30 today is a closed day and `lastClosedWeekdays` already counts it. */
export function todayInSession(now: Date = new Date()): string | null {
  const ist = new Date(now.getTime() + 330 * 60_000);
  const wd = ist.getUTCDay();
  const min = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  if (wd === 0 || wd === 6 || min < 9 * 60 + 20 || min >= 15 * 60 + 30) return null;
  return ist.toISOString().slice(0, 10);
}
