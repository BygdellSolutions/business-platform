import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { JournalSummary } from "@/lib/api/types";
import { JournalTable } from "./JournalTable";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const HORSE = "99999999-9999-4999-8999-999999999999";
const row = (number: number, overrides: Partial<JournalSummary> = {}): JournalSummary => ({
  id: `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`, number, horse_id: HORSE, visit_at: "2026-10-15T12:30:00Z", practitioner_name: "Tina Therapist",
  status: "draft", follow_up_on: null, mark_count: 2, preview: "Stiff left shoulder", version: 1, created_by: null, created_by_name: null, ...overrides,
});

describe("the journal table", () => {
  it("has the columns No., Visit, By, Status, Marks and Notes, one row per entry", () => {
    render(<JournalTable orgId={ORG} horseId={HORSE} entries={[row(12), row(11, { status: "final", mark_count: 0, preview: "Back to work" })]} timeZone="Europe/Stockholm" />);
    const headings = within(screen.getByTestId("journal-table")).getAllByRole("columnheader").map((heading) => heading.textContent?.trim());
    expect(headings).toEqual(["No.", "Visit", "By", "Status", "Marks", "Notes"]);
    const rows = screen.getAllByTestId("journal-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByTestId("record-number").textContent).toBe("12");
    expect(within(rows[0]).getByTestId("entry-visit").textContent).toBe("2026-10-15 14:30"); // in the organization's time zone
    expect(within(rows[0]).getByTestId("entry-status").textContent).toBe("Draft");
    expect(within(rows[1]).getByTestId("entry-status").textContent).toBe("Finalized");
    expect(rows[1].textContent).toContain("Back to work");
  });

  it("opens the entry from its number", () => {
    render(<JournalTable orgId={ORG} horseId={HORSE} entries={[row(12)]} timeZone={null} />);
    expect(screen.getByTestId("journal-entry-link").getAttribute("href")).toBe(`/o/${ORG}/horses/${HORSE}/journal/${row(12).id}`);
  });

  it("sorts by a column through its heading when it is given the addresses, and has plain headings otherwise", () => {
    const hrefs = { number: "/n", visit: "/v", by: "/b", status: "/s", marks: "/m" };
    const { unmount } = render(<JournalTable orgId={ORG} horseId={HORSE} entries={[row(1)]} timeZone={null} sort={{ current: "visit", dir: "desc", hrefs }} />);
    expect(screen.getByRole("link", { name: /Visit/ }).getAttribute("href")).toBe("/v");
    expect(screen.getByRole("columnheader", { name: /Visit/ }).getAttribute("aria-sort")).toBe("descending");
    unmount();
    render(<JournalTable orgId={ORG} horseId={HORSE} entries={[row(1)]} timeZone={null} />);
    expect(screen.queryByRole("link", { name: /Visit/ })).toBeNull();
  });
});
