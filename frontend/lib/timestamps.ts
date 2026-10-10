/**
 * A stored timestamp (ISO, UTC) shown in the organization's time zone, as "2026-10-08 14:02". Without a zone it
 * is shown in UTC and says so. Display only: timestamps are never parsed back or computed with.
 */
export function formatTimestamp(iso: string, timeZone: string | null): string {
  const shown = new Intl.DateTimeFormat("sv-SE", {
    timeZone: timeZone ?? "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
  return timeZone ? shown : `${shown} UTC`;
}

/** The calendar day of a stored timestamp in the organization's time zone ("2026-10-08"; UTC without a zone). */
export function formatDay(iso: string, timeZone: string | null): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: timeZone ?? "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(iso));
}
