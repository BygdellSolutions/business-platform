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
