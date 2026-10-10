import { describe, expect, it } from "vitest";

import { formatShortDate, isDateShape, localToday } from "@/lib/dates";

describe("localToday", () => {
  it("is the browser's local calendar date as YYYY-MM-DD, not the UTC one", () => {
    // 23:30 local on 31 December: a UTC conversion could already say the 1st of January.
    expect(localToday(new Date(2026, 11, 31, 23, 30))).toBe("2026-12-31");
    expect(localToday(new Date(2026, 0, 1, 0, 5))).toBe("2026-01-01");
  });

  it("pads month and day", () => {
    expect(localToday(new Date(2026, 2, 4))).toBe("2026-03-04");
  });

  it("is a plain string", () => {
    expect(typeof localToday()).toBe("string");
    expect(isDateShape(localToday())).toBe(true);
  });
});

describe("isDateShape", () => {
  it.each(["2026-10-03", "1999-01-31", "2026-13-45"])("accepts the shape %s (whether it is a real day is the backend's call)", (value) => {
    expect(isDateShape(value)).toBe(true);
  });

  it.each(["", "2026-1-3", "03/10/2026", "2026-10-03T10:00:00Z", " 2026-10-03", "20261003", "abcd-ef-gh"])("rejects %j", (value) => {
    expect(isDateShape(value)).toBe(false);
  });
});

describe("formatShortDate", () => {
  it("shows day and month, and the year only when it is not this year's", () => {
    expect(formatShortDate("2026-10-10", "2026-05-01")).toBe("10 Oct");
    expect(formatShortDate("2026-10-01", "2026-05-01")).toBe("1 Oct");
    expect(formatShortDate("2027-01-03", "2026-05-01")).toBe("3 Jan 2027");
    expect(formatShortDate("not a date", "2026-05-01")).toBe("not a date");
  });
});
