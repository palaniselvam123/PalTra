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
