/**
 * Calendar dates travel as plain `YYYY-MM-DD` strings (what the backend's `date` fields and
 * `<input type="date">` use). They are never turned into Date objects for anything but asking
 * the browser what day it is, so no time zone can shift a stored date.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Only the SHAPE of a date. Whether it is a real day is the backend's decision. */
export function isDateShape(value: string): boolean {
  return DATE.test(value);
}

/** Today's date in the BROWSER's time zone, as `YYYY-MM-DD`. */
export function localToday(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/** The calendar day `days` after `day` ("YYYY-MM-DD"), as a calendar date (no time zone involved). */
export function addDays(day: string, days: number): string {
  const moment = new Date(`${day}T00:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + days);
  return moment.toISOString().slice(0, 10);
}

const MONTHS: Record<string, string> = {
  "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
  "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
};

/** "2026-10-10" as "10 Oct" (with the year when it is not this year's: "3 Jan 2027"). Text only, no Date object. */
export function formatShortDate(day: string, today: string = localToday()): string {
  if (!isDateShape(day)) return day;
  const [year, month, date] = day.split("-");
  const short = `${date.replace(/^0/, "")} ${MONTHS[month] ?? month}`;
  return year === today.slice(0, 4) ? short : `${short} ${year}`;
}
