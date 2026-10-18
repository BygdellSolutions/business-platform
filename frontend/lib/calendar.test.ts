import { describe, expect, it } from "vitest";

import { isoWeek, weekLabel, clashing, dayDifference, daysOf, hourRange, isRealDay, localParts, minutesOf, mondayOf, movedTo, place, resizedTo, shiftedByDays, snapMinutes, step, titleOf, windowOf, zonedInstant, type Booking } from "@/lib/calendar";

const STOCKHOLM = "Europe/Stockholm";

describe("zonedInstant / localParts", () => {
  it("reads the wall clock of the zone, in summer and winter time", () => {
    expect(zonedInstant("2026-10-19", "09:00", STOCKHOLM)).toBe("2026-10-19T07:00:00.000Z"); // UTC+2
    expect(zonedInstant("2026-12-01", "09:00", STOCKHOLM)).toBe("2026-12-01T08:00:00.000Z"); // UTC+1
    expect(zonedInstant("2026-10-19", "09:00", null)).toBe("2026-10-19T09:00:00.000Z"); // no zone: UTC
    expect(zonedInstant("2026-10-19", "03:00", "America/New_York")).toBe("2026-10-19T07:00:00.000Z");
  });

  it("is the inverse of localParts, also around the clock changes", () => {
    for (const [day, time] of [["2026-10-19", "09:30"], ["2026-03-29", "01:30"], ["2026-03-29", "04:00"], ["2026-10-25", "01:30"], ["2026-10-25", "04:00"], ["2026-01-01", "00:00"]]) {
      const back = localParts(zonedInstant(day, time, STOCKHOLM), STOCKHOLM);
      expect(`${back.day} ${String(Math.floor(back.minutes / 60)).padStart(2, "0")}:${String(back.minutes % 60).padStart(2, "0")}`).toBe(`${day} ${time}`);
    }
  });

  it("puts a time near midnight on the right day in the zone, not in UTC", () => {
    expect(localParts("2026-10-18T22:30:00Z", STOCKHOLM)).toEqual({ day: "2026-10-19", minutes: 30 }); // 00:30 the next day in Stockholm
  });

  it("moves a time that does not exist (spring forward) ahead instead of failing", () => {
    expect(localParts(zonedInstant("2026-03-29", "02:30", STOCKHOLM), STOCKHOLM).day).toBe("2026-03-29");
  });
});

describe("days and windows", () => {
  it("weeks start on Monday", () => {
    expect(mondayOf("2026-10-19")).toBe("2026-10-19"); // a Monday
    expect(mondayOf("2026-10-25")).toBe("2026-10-19"); // the Sunday after
    expect(daysOf("week", "2026-10-22")).toEqual(["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"]);
  });

  it("a month shows six whole weeks that include the whole month", () => {
    const days = daysOf("month", "2026-10-16");
    expect(days).toHaveLength(42);
    expect(days[0]).toBe("2026-09-28");
    expect(days).toContain("2026-10-31");
    expect(daysOf("month", "2026-02-10")[0]).toBe("2026-01-26");
  });

  it("asks for whole local days, which is 23 or 25 hours on a clock change day", () => {
    expect(windowOf("day", "2026-10-19", STOCKHOLM)).toEqual({ from: "2026-10-18T22:00:00.000Z", to: "2026-10-19T22:00:00.000Z" });
    const long = windowOf("day", "2026-10-25", STOCKHOLM);
    expect((new Date(long.to).getTime() - new Date(long.from).getTime()) / 3600000).toBe(25);
    const month = windowOf("month", "2026-10-16", STOCKHOLM);
    expect((new Date(month.to).getTime() - new Date(month.from).getTime()) / 86400000).toBeLessThanOrEqual(62); // the backend refuses more
  });

  it("steps by a day, a week or a month", () => {
    expect(step("day", "2026-10-31", 1)).toBe("2026-11-01");
    expect(step("week", "2026-10-19", -1)).toBe("2026-10-12");
    expect(step("month", "2026-10-31", 1)).toBe("2026-11-01");
    expect(step("month", "2026-01-15", -1)).toBe("2025-12-01");
  });

  it("titles say what is shown", () => {
    expect(titleOf("month", "2026-10-16")).toBe("October 2026");
    expect(titleOf("day", "2026-10-19")).toBe("Mon 19 October 2026");
    expect(titleOf("week", "2026-10-21")).toBe("19–25 October 2026");
    expect(titleOf("week", "2026-10-29")).toBe("26 Oct – 1 Nov 2026");
  });

  it("recognizes real days and times", () => {
    expect(isRealDay("2026-02-29")).toBe(false);
    expect(isRealDay("2026-10-19")).toBe(true);
    expect(isRealDay("tomorrow")).toBe(false);
    expect(minutesOf("09:30")).toBe(570);
    expect(minutesOf("24:00")).toBeNull();
    expect(minutesOf("9:30")).toBeNull();
  });
});

const b = (id: string, from: string, to: string, who: string | null = "anna"): Booking => ({ id, starts_at: `2026-10-19T${from}:00Z`, ends_at: `2026-10-19T${to}:00Z`, practitioner_user_id: who });

describe("clashing", () => {
  it("flags bookings that overlap for the same person only", () => {
    const found = clashing([b("a", "09:00", "10:00"), b("b", "09:30", "10:30"), b("c", "10:30", "11:00"), b("d", "09:00", "10:00", "bo"), b("e", "09:15", "09:45", null), b("f", "09:20", "09:50", null)]);
    expect([...found].sort()).toEqual(["a", "b"]); // c starts when b ends; d is someone else's; unassigned bookings never clash
  });

  it("finds a long booking that overlaps several short ones", () => {
    expect([...clashing([b("long", "09:00", "12:00"), b("x", "10:00", "10:30"), b("y", "11:00", "11:30")])].sort()).toEqual(["long", "x", "y"]);
  });
});

describe("place", () => {
  it("puts overlapping bookings side by side and the others full width", () => {
    const placed = place([b("a", "09:00", "10:00"), b("b", "09:30", "10:30"), b("c", "11:00", "12:00")], "2026-10-19", "UTC");
    const by = Object.fromEntries(placed.map((p) => [p.item.id, p]));
    expect([by.a.lane, by.a.lanes, by.b.lane, by.b.lanes]).toEqual([0, 2, 1, 2]);
    expect([by.c.lane, by.c.lanes]).toEqual([0, 1]);
    expect([by.a.top, by.a.height]).toEqual([540, 60]);
  });

  it("clips a booking to the day and leaves out the ones outside it", () => {
    const night: Booking = { id: "n", starts_at: "2026-10-18T22:00:00Z", ends_at: "2026-10-19T02:00:00Z", practitioner_user_id: null };
    const [first] = place([night], "2026-10-19", "UTC");
    expect([first.top, first.height]).toEqual([0, 120]);
    expect(place([night], "2026-10-20", "UTC")).toEqual([]);
  });

  it("widens the hour range to include early and late bookings", () => {
    expect(hourRange([])).toEqual({ first: 7, last: 19 });
    expect(hourRange([{ top: 5 * 60, height: 60 }, { top: 20 * 60, height: 90 }])).toEqual({ first: 5, last: 22 });
  });
});

describe("moving and resizing a booking", () => {
  const start = "2026-10-19T07:00:00.000Z"; // 09:00 in Stockholm
  const end = "2026-10-19T08:30:00.000Z"; // 10:30

  it("snaps to quarter hours and stays inside the day", () => {
    expect([snapMinutes(547), snapMinutes(553), snapMinutes(-30), snapMinutes(24 * 60 + 100)]).toEqual([540, 555, 0, 24 * 60 - 15]);
  });

  it("moves to another time and day on the organization's wall clock and keeps the length", () => {
    expect(movedTo(start, end, "2026-10-20", 11 * 60, STOCKHOLM)).toEqual({ starts_at: "2026-10-20T09:00:00.000Z", ends_at: "2026-10-20T10:30:00.000Z" });
    expect(movedTo(start, end, "2026-10-19", 9 * 60, STOCKHOLM)).toEqual({ starts_at: start, ends_at: end }); // dropped where it was: the same instants
    expect(localParts(movedTo(start, end, "2026-10-19", 23 * 60 + 30, STOCKHOLM).ends_at, STOCKHOLM)).toEqual({ day: "2026-10-20", minutes: 60 }); // may run past midnight (to 01:00)
  });

  it("keeps the wall-clock time when it moves over a clock change", () => {
    const moved = shiftedByDays(start, end, 7, STOCKHOLM); // 26 October: winter time has begun
    expect(localParts(moved.starts_at, STOCKHOLM)).toEqual({ day: "2026-10-26", minutes: 9 * 60 });
    expect(new Date(moved.ends_at).getTime() - new Date(moved.starts_at).getTime()).toBe(90 * 60000);
    expect(shiftedByDays(start, end, -2, STOCKHOLM).starts_at).toBe("2026-10-17T07:00:00.000Z");
  });

  it("counts whole days between two days", () => {
    expect([dayDifference("2026-10-19", "2026-10-22"), dayDifference("2026-10-22", "2026-10-19"), dayDifference("2026-10-25", "2026-10-26"), dayDifference("2026-10-19", "2026-10-19")]).toEqual([3, -3, 1, 0]);
  });

  it("resizes the end, refuses an end that is not after the start, and reaches midnight", () => {
    expect(resizedTo(start, "2026-10-19", 12 * 60, STOCKHOLM)).toBe("2026-10-19T10:00:00.000Z");
    expect(resizedTo(start, "2026-10-19", 9 * 60, STOCKHOLM)).toBeNull(); // not after the start
    expect(resizedTo(start, "2026-10-19", 8 * 60, STOCKHOLM)).toBeNull();
    expect(resizedTo(start, "2026-10-19", 24 * 60, STOCKHOLM)).toBe("2026-10-19T22:00:00.000Z"); // the end of the day
  });
});

describe("week numbers", () => {
  it("follows ISO 8601, also around the new year", () => {
    expect(isoWeek("2026-10-19")).toBe(43);
    expect(isoWeek("2026-10-25")).toBe(43); // Sunday ends the week
    expect(isoWeek("2026-10-26")).toBe(44);
    expect(isoWeek("2026-01-01")).toBe(1); // a Thursday: week 1
    expect(isoWeek("2027-01-01")).toBe(53); // a Friday, still in 2026's last week
    expect(isoWeek("2024-12-30")).toBe(1); // belongs to 2025's week 1
    expect(isoWeek("2020-12-31")).toBe(53);
  });

  it("labels a day or week by its week and a month by the weeks it touches", () => {
    expect(weekLabel("day", "2026-10-21")).toBe("Week 43");
    expect(weekLabel("week", "2026-10-26")).toBe("Week 44");
    expect(weekLabel("month", "2026-10-15")).toBe("Weeks 40–44");
    expect(weekLabel("month", "2026-12-10")).toBe("Weeks 49–53");
  });
});
