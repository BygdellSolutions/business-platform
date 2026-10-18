/**
 * The calendar's date arithmetic. A day is a plain `YYYY-MM-DD` (see lib/dates.ts) and a moment is an ISO instant; the two meet only here,
 * through the ORGANIZATION's time zone (never the browser's), so everyone in an organization sees the same hours.
 */

import { addDays } from "@/lib/dates";

export type CalendarView = "day" | "week" | "month";

export const VIEWS: readonly CalendarView[] = ["day", "week", "month"];

/** The offset of `zone` from UTC at an instant, in minutes (positive east of Greenwich). */
function offsetMinutes(ms: number, zone: string): number {
  const p = partsOf(ms, zone);
  return (Date.UTC(p.year, p.month - 1, p.date, p.hour, p.minute) - Math.floor(ms / 60000) * 60000) / 60000;
}

function partsOf(ms: number, zone: string) {
  const fields = new Intl.DateTimeFormat("en-GB", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(new Date(ms));
  const get = (type: string) => Number(fields.find((field) => field.type === type)?.value);
  return { year: get("year"), month: get("month"), date: get("day"), hour: get("hour"), minute: get("minute") };
}

/** The instant (ISO, UTC) at which the wall clock in `zone` reads `day` `time` ("HH:mm"). A time that does not exist (spring forward) moves ahead. */
export function zonedInstant(day: string, time: string, zone: string | null): string {
  const [y, m, d] = day.split("-").map(Number);
  const [h, min] = time.split(":").map(Number);
  const wall = Date.UTC(y, m - 1, d, h, min);
  const tz = zone ?? "UTC";
  let ms = wall - offsetMinutes(wall, tz) * 60000;
  ms = wall - offsetMinutes(ms, tz) * 60000; // once more: the offset at the first guess may differ from the one at the answer
  return new Date(ms).toISOString();
}

/** The day and the minutes since midnight at which `iso` falls on the wall clock of `zone`. */
export function localParts(iso: string, zone: string | null): { day: string; minutes: number } {
  const p = partsOf(new Date(iso).getTime(), zone ?? "UTC");
  const day = `${String(p.year).padStart(4, "0")}-${String(p.month).padStart(2, "0")}-${String(p.date).padStart(2, "0")}`;
  return { day, minutes: p.hour * 60 + p.minute };
}

export function timeText(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/** "HH:mm" as minutes since midnight, or null when it is not a time. */
export function minutesOf(time: string): number | null {
  const found = /^(\d{2}):(\d{2})$/.exec(time);
  if (!found) return null;
  const minutes = Number(found[1]) * 60 + Number(found[2]);
  return Number(found[1]) < 24 && Number(found[2]) < 60 ? minutes : null;
}

export function isRealDay(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
}

/** 0 = Monday ... 6 = Sunday. */
function weekday(day: string): number {
  return (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7;
}

export function mondayOf(day: string): string {
  return addDays(day, -weekday(day));
}

export function firstOfMonth(day: string): string {
  return `${day.slice(0, 8)}01`;
}

/** The first day of the month `months` away from `day`'s month. */
function monthsFrom(day: string, months: number): string {
  const [y, m] = day.split("-").map(Number);
  const moment = new Date(Date.UTC(y, m - 1 + months, 1));
  return moment.toISOString().slice(0, 10);
}

/** The days a view shows: one for a day, Monday to Sunday for a week, six weeks starting at the Monday on or before the 1st for a month. */
export function daysOf(view: CalendarView, day: string): string[] {
  if (view === "day") return [day];
  const start = mondayOf(view === "week" ? day : firstOfMonth(day));
  return Array.from({ length: view === "week" ? 7 : 42 }, (_, index) => addDays(start, index));
}

/** The ISO 8601 week number of a day (weeks start on Monday and week 1 holds the year's first Thursday), as used in Sweden. */
export function isoWeek(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7)); // the Thursday of this week decides the year
  const yearStart = Date.UTC(date.getUTCFullYear(), 0, 1);
  return Math.ceil(((date.getTime() - yearStart) / 86400000 + 1) / 7);
}

/** "Week 43" for a day or a week, "Weeks 40–44" for the weeks a month touches. */
export function weekLabel(view: CalendarView, day: string): string {
  if (view !== "month") return `Week ${isoWeek(day)}`;
  const first = firstOfMonth(day);
  const last = addDays(firstOfMonth(addDays(first, 31)), -1);
  return `Weeks ${isoWeek(first)}–${isoWeek(last)}`;
}

/** What to ask the backend for: from the start of the first day to the start of the day after the last. */
export function windowOf(view: CalendarView, day: string, zone: string | null): { from: string; to: string } {
  const days = daysOf(view, day);
  return { from: zonedInstant(days[0], "00:00", zone), to: zonedInstant(addDays(days[days.length - 1], 1), "00:00", zone) };
}

/** The day the previous/next button leads to. */
export function step(view: CalendarView, day: string, direction: -1 | 1): string {
  if (view === "day") return addDays(day, direction);
  if (view === "week") return addDays(day, 7 * direction);
  return monthsFrom(day, direction);
}

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function weekdayName(day: string): string {
  return WEEKDAY_NAMES[weekday(day)];
}

export function dayOfMonth(day: string): number {
  return Number(day.slice(8));
}

export function monthName(day: string): string {
  return MONTH_NAMES[Number(day.slice(5, 7)) - 1];
}

export function titleOf(view: CalendarView, day: string): string {
  if (view === "month") return `${monthName(day)} ${day.slice(0, 4)}`;
  if (view === "day") return `${weekdayName(day)} ${dayOfMonth(day)} ${monthName(day)} ${day.slice(0, 4)}`;
  const days = daysOf("week", day);
  const [first, last] = [days[0], days[6]];
  const span = first.slice(0, 7) === last.slice(0, 7) ? `${dayOfMonth(first)}–${dayOfMonth(last)} ${monthName(last)}` : `${dayOfMonth(first)} ${monthName(first).slice(0, 3)} – ${dayOfMonth(last)} ${monthName(last).slice(0, 3)}`;
  return `${span} ${last.slice(0, 4)}`;
}

export interface Booking {
  id: string;
  starts_at: string;
  ends_at: string;
  practitioner_user_id: string | null;
}

/** The bookings that overlap another booking of the SAME person (one person cannot be in two places). Unassigned bookings never clash. */
export function clashing(bookings: readonly Booking[]): Set<string> {
  const clashes = new Set<string>();
  const byPerson = new Map<string, Booking[]>();
  for (const booking of bookings) {
    if (booking.practitioner_user_id === null) continue;
    byPerson.set(booking.practitioner_user_id, [...(byPerson.get(booking.practitioner_user_id) ?? []), booking]);
  }
  for (const own of byPerson.values()) {
    const sorted = [...own].sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.id.localeCompare(b.id));
    for (let i = 0; i < sorted.length; i++) {
      for (let j = i + 1; j < sorted.length && new Date(sorted[j].starts_at) < new Date(sorted[i].ends_at); j++) {
        clashes.add(sorted[i].id);
        clashes.add(sorted[j].id);
      }
    }
  }
  return clashes;
}

export interface Placed<T> {
  item: T;
  /** Minutes from the start of the day, clipped to the day. */
  top: number;
  height: number;
  /** Side-by-side position among bookings that overlap in time. */
  lane: number;
  lanes: number;
}

/** Positions the bookings that touch `day` in a column of that day: clipped to the day, overlapping ones side by side. */
export function place<T extends Booking>(items: readonly T[], day: string, zone: string | null): Placed<T>[] {
  const dayStart = new Date(zonedInstant(day, "00:00", zone)).getTime();
  const dayEnd = new Date(zonedInstant(addDays(day, 1), "00:00", zone)).getTime();
  const clipped = items
    .map((item) => ({ item, start: Math.max(new Date(item.starts_at).getTime(), dayStart), end: Math.min(new Date(item.ends_at).getTime(), dayEnd) }))
    .filter((entry) => entry.end > entry.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);

  const result: Placed<T>[] = [];
  let group: { entry: (typeof clipped)[number]; lane: number }[] = [];
  let groupEnd = 0;
  const flush = () => {
    const lanes = Math.max(1, ...group.map((member) => member.lane + 1));
    for (const { entry, lane } of group) {
      result.push({ item: entry.item, top: (entry.start - dayStart) / 60000, height: (entry.end - entry.start) / 60000, lane, lanes });
    }
    group = [];
  };
  for (const entry of clipped) {
    if (group.length > 0 && entry.start >= groupEnd) flush();
    const taken = new Set(group.filter((member) => member.entry.end > entry.start).map((member) => member.lane));
    let lane = 0;
    while (taken.has(lane)) lane++;
    group.push({ entry, lane });
    groupEnd = Math.max(groupEnd, entry.end);
  }
  flush();
  return result;
}

/** The hours a time grid covers: the working day by default, widened to include every booking. */
export function hourRange(placed: readonly { top: number; height: number }[], early = 7, late = 19): { first: number; last: number } {
  let first = early;
  let last = late;
  for (const entry of placed) {
    first = Math.min(first, Math.floor(entry.top / 60));
    last = Math.max(last, Math.ceil((entry.top + entry.height) / 60));
  }
  return { first: Math.max(0, first), last: Math.min(24, last) };
}

/** Dragging snaps to this many minutes. */
export const SNAP_MINUTES = 15;

/** `minutes` (since midnight) rounded to the nearest step, kept inside the day (the last start is one step before midnight). */
export function snapMinutes(minutes: number, step = SNAP_MINUTES): number {
  return Math.min(24 * 60 - step, Math.max(0, Math.round(minutes / step) * step));
}

/** The whole days from `from` to `to` (both "YYYY-MM-DD"; negative when `to` is earlier). */
export function dayDifference(from: string, to: string): number {
  return Math.round((new Date(`${to}T00:00:00Z`).getTime() - new Date(`${from}T00:00:00Z`).getTime()) / 86400000);
}

/** A booking moved so that it starts at `minutes` on `day` (the organization's wall clock); its length stays the same. */
export function movedTo(startsAt: string, endsAt: string, day: string, minutes: number, zone: string | null): { starts_at: string; ends_at: string } {
  const length = new Date(endsAt).getTime() - new Date(startsAt).getTime();
  const start = zonedInstant(day, timeText(minutes), zone);
  return { starts_at: start, ends_at: new Date(new Date(start).getTime() + length).toISOString() };
}

/** A booking moved by whole days, at the same time of day on the wall clock (so a move over a clock change keeps "09:00" as 09:00). */
export function shiftedByDays(startsAt: string, endsAt: string, days: number, zone: string | null): { starts_at: string; ends_at: string } {
  const start = localParts(startsAt, zone);
  return movedTo(startsAt, endsAt, addDays(start.day, days), start.minutes, zone);
}

/** The end of a booking dragged to `minutes` on the day it starts, or null when that would not be after its start. */
export function resizedTo(startsAt: string, day: string, minutes: number, zone: string | null): string | null {
  const end = minutes >= 24 * 60 ? zonedInstant(addDays(day, 1), "00:00", zone) : zonedInstant(day, timeText(minutes), zone);
  return new Date(end) > new Date(startsAt) ? end : null;
}
