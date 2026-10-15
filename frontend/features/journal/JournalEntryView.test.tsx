import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import type { ApiResult } from "@/lib/api/errors";
import type { JournalEntry } from "@/lib/api/types";
import { JournalEntryView } from "./JournalEntryView";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const HORSE = "99999999-9999-4999-8999-999999999999";
const ENTRY = "88888888-8888-4888-8888-888888888888";
const mocked = vi.mocked(apiFetch);
const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, status: 201, data });

function entry(overrides: Partial<JournalEntry> = {}): JournalEntry {
  return {
    id: ENTRY, horse_id: HORSE, visit_at: "2026-10-15T12:30:00Z", practitioner_user_id: null, practitioner_name: "Tina Therapist", body: "Treated the poll.", follow_up_on: "2026-10-29",
    marks: [{ id: "11111111-1111-4111-8111-111111111111", side: "right", tool: "o", points: [[0.5, 0.5]] }], diagram_version: 1, status: "final", version: 3,
    finalized_at: "2026-10-15T13:00:00Z", finalized_by: null, created_at: "", updated_at: "", created_by: null, updated_by: null, created_by_name: "Tina Therapist",
    finalized_by_name: "Tina Therapist", can_change: false, can_correct: true, corrections: [], ...overrides,
  };
}

beforeEach(() => {
  mocked.mockReset();
  router.refresh.mockReset();
});

const mount = (value: JournalEntry) =>
  render(
    <OrgScope orgId={ORG}>
      <JournalEntryView entry={value} timeZone="Europe/Stockholm" />
    </OrgScope>,
  );

describe("a finalized entry", () => {
  it("shows what was frozen, who finalized it and when, with the marks of each side in words", () => {
    mount(entry());
    expect(screen.getByTestId("entry-body").textContent).toBe("Treated the poll.");
    expect(screen.getByTestId("entry-final").textContent).toContain("by Tina Therapist");
    expect(screen.getByTestId("entry-follow-up").textContent).toContain("2026-10-29");
    expect(screen.getByText("O on the horse's right side, at 50 % across, 50 % down")).toBeTruthy();
    expect(screen.getByTestId("no-corrections")).toBeTruthy();
  });

  it("adds a correction as a dated addition and offers no way to edit what was written", async () => {
    mocked.mockResolvedValueOnce(ok(entry()));
    mount(entry());
    expect(screen.queryByTestId("save-draft")).toBeNull();
    await userEvent.type(screen.getByLabelText(/Add a correction/), "It was the other side.");
    await userEvent.click(screen.getByTestId("add-correction"));
    expect(mocked).toHaveBeenCalledWith(ORG, `/horses/${HORSE}/journal/${ENTRY}/corrections`, { method: "POST", body: { body: "It was the other side." } });
    await waitFor(() => expect(router.refresh).toHaveBeenCalled());
  });

  it("lists the corrections with their author, and offers the form only to whoever may correct", () => {
    const corrected = entry({ can_correct: false, corrections: [{ id: "c1", entry_id: ENTRY, body: "Left, not right.", created_at: "2026-10-16T08:00:00Z", created_by: null, created_by_name: "Admin Person" }] });
    mount(corrected);
    expect(screen.getByTestId("correction").textContent).toContain("Admin Person");
    expect(screen.getByTestId("correction").textContent).toContain("Left, not right.");
    expect(screen.queryByTestId("add-correction")).toBeNull();
  });

  it("says plainly that a draft someone else is writing is not finalized", () => {
    mount(entry({ status: "draft", finalized_at: null, finalized_by_name: null, can_correct: false }));
    expect(screen.getByTestId("entry-draft")).toBeTruthy();
    expect(screen.queryByTestId("entry-final")).toBeNull();
  });
});
