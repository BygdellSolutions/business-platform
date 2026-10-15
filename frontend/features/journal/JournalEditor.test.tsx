import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { normalizeError, type ApiResult } from "@/lib/api/errors";
import type { JournalEntry } from "@/lib/api/types";
import { JournalEditor } from "./JournalEditor";

const router = { push: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const HORSE = "99999999-9999-4999-8999-999999999999";
const ENTRY = "88888888-8888-4888-8888-888888888888";
const mocked = vi.mocked(apiFetch);
const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, status: 200, data });
const fail = <T,>(status: number, body: unknown): ApiResult<T> => ({ ok: false, error: normalizeError(status, body) });

function entry(overrides: Partial<JournalEntry> = {}): JournalEntry {
  return {
    id: ENTRY, number: 12, horse_id: HORSE, visit_at: "2026-10-15T12:30:00Z", practitioner_user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", practitioner_name: "Tina Therapist", body: "", follow_up_on: null, marks: [],
    diagram_version: 1, status: "draft", version: 1, finalized_at: null, finalized_by: null, created_at: "", updated_at: "", created_by: null, updated_by: null,
    created_by_name: "Tina Therapist", finalized_by_name: null, can_change: true, can_correct: false, corrections: [], ...overrides,
  };
}

const COLLEAGUES = [
  { user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Tina Therapist" },
  { user_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Erik Employee" },
];
const answers: ApiResult<unknown>[] = [];
/** The calls the editor made about the entry itself (not the list of colleagues it loads). */
const calls = () => mocked.mock.calls.filter((call) => !String(call[1]).startsWith("/members/people"));

beforeEach(() => {
  mocked.mockReset();
  answers.length = 0;
  mocked.mockImplementation((async (_org: string, path: string) => (path.startsWith("/members/people") ? ok(COLLEAGUES) : (answers.shift() ?? fail(500, {})))) as never);
  router.refresh.mockReset();
  router.push.mockReset();
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300, x: 0, y: 0, toJSON: () => ({}) });
});
afterEach(() => vi.restoreAllMocks());

const mount = (value = entry()) =>
  render(
    <OrgScope orgId={ORG}>
      <JournalEditor entry={value} />
    </OrgScope>,
  );
const status = () => screen.getByTestId("save-status").textContent;
const tap = (x: number, y: number) => fireEvent.pointerDown(screen.getByTestId("diagram-left"), { clientX: x, clientY: y, pointerId: 1 });

describe("writing an entry", () => {
  it("says there are no unsaved changes until something changes, and then says so", async () => {
    mount();
    expect(status()).toBe("All changes saved");
    expect((screen.getByTestId("save-draft") as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText(/Observations/), "Stiff left shoulder");
    expect(status()).toBe("Unsaved changes");
    expect((screen.getByTestId("save-draft") as HTMLButtonElement).disabled).toBe(false);
  });

  it("saves only what changed, against the version the draft was opened on, and then shows it saved", async () => {
    answers.push(ok(entry({ body: "Stiff", version: 2 })));
    mount();
    await userEvent.type(screen.getByLabelText(/Observations/), "Stiff");
    await userEvent.click(screen.getByTestId("save-draft"));
    expect(calls()).toEqual([[ORG, `/horses/${HORSE}/journal/${ENTRY}`, { method: "PATCH", body: { body: "Stiff" }, ifMatch: 1 }]]);
    await waitFor(() => expect(status()).toBe("All changes saved"));
    expect(screen.getByTestId("saved").textContent).toBe("Draft saved.");
  });

  it("draws a mark with a tool, lists it in words, and can undo, redo and delete it", async () => {
    mount();
    await userEvent.click(screen.getByTestId("tool-x"));
    tap(100, 75);
    expect(screen.getByTestId("marks-list").textContent).toContain("X on the horse's left side, at 25 % across, 25 % down");
    expect(status()).toBe("Unsaved changes");

    await userEvent.click(screen.getByTestId("undo"));
    expect(screen.getByTestId("no-marks")).toBeTruthy();
    expect(status()).toBe("All changes saved"); // back to what is saved
    await userEvent.click(screen.getByTestId("redo"));
    expect(screen.getAllByTestId("mark")).toHaveLength(1);
    await userEvent.click(screen.getByTestId("delete-listed-mark"));
    expect(screen.queryByTestId("marks-list")).toBeNull();
  });

  it("keeps the marks of each side apart", async () => {
    mount();
    await userEvent.click(screen.getByTestId("tool-o"));
    tap(100, 75);
    await userEvent.click(screen.getByTestId("side-right"));
    expect(screen.getByTestId("no-marks")).toBeTruthy();
    expect(screen.getByTestId("diagram-right")).toBeTruthy();
    await userEvent.click(screen.getByTestId("side-left"));
    expect(screen.getAllByTestId("mark")).toHaveLength(1);
  });

  it("sends the marks with the save", async () => {
    answers.push(ok(entry({ version: 2 })));
    mount();
    await userEvent.click(screen.getByTestId("tool-x"));
    tap(200, 150);
    await userEvent.click(screen.getByTestId("save-draft"));
    const body = (calls()[0][2] as { body: { marks: { tool: string; points: number[][] }[] } }).body;
    expect(body.marks).toHaveLength(1);
    expect(body.marks[0]).toMatchObject({ tool: "x", points: [[0.5, 0.5]] });
  });
});

describe("saving marks", () => {
  it("is shown as saved when the backend answers with the same marks written in its own field order", async () => {
    mount();
    await userEvent.click(screen.getByTestId("tool-x"));
    tap(200, 150);
    const drawn = (JSON.parse(JSON.stringify(document.querySelector('[data-testid="mark"]')?.getAttribute("data-mark-id") ?? "")) as string);
    // the backend writes id, number, side, tool, points (the browser built them in another order)
    answers.push(ok(entry({ version: 2, marks: [{ points: [[0.5, 0.5]], tool: "x", side: "left", number: 1, id: drawn } as never] })));
    await userEvent.click(screen.getByTestId("save-draft"));
    await waitFor(() => expect(status()).toBe("All changes saved"));
  });
});

describe("the visit date", () => {
  it("is a day, not a time: shown as a date, and a changed day is saved as noon of that day", async () => {
    answers.push(ok(entry({ visit_at: "2026-11-03T12:00:00Z", version: 2 })));
    mount();
    const field = screen.getByLabelText("Visit date") as HTMLInputElement;
    expect(field.type).toBe("date");
    expect(field.value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(screen.queryByLabelText(/Visit date and time/)).toBeNull();
    fireEvent.change(field, { target: { value: "2026-11-03" } });
    await userEvent.click(screen.getByTestId("save-draft"));
    const sent = (calls()[0][2] as { body: { visit_at: string } }).body.visit_at;
    expect(new Date(sent).getHours()).toBe(12); // noon in the browser's own time zone
    expect(sent.startsWith("2026-11-03") || sent.startsWith("2026-11-02") || sent.startsWith("2026-11-04")).toBe(true);
  });
});

describe("the practitioner", () => {
  it("is chosen from the organization's members, like who performed a service on an order, and saved by id", async () => {
    answers.push(ok(entry({ practitioner_user_id: COLLEAGUES[1].user_id, practitioner_name: "Erik Employee", version: 2 })));
    mount();
    const select = (await screen.findByLabelText("Practitioner")) as HTMLSelectElement;
    await waitFor(() => expect([...select.options].map((option) => option.text)).toEqual(["Tina Therapist", "Erik Employee"]));
    expect(select.value).toBe(COLLEAGUES[0].user_id); // the author unless someone else is chosen
    await userEvent.selectOptions(select, COLLEAGUES[1].user_id);
    expect(status()).toBe("Unsaved changes");
    await userEvent.click(screen.getByTestId("save-draft"));
    expect(calls()[0][2]).toMatchObject({ method: "PATCH", body: { practitioner_user_id: COLLEAGUES[1].user_id }, ifMatch: 1 });
  });

  it("asks only for the people who work with the journal, and offers nobody else", async () => {
    mount();
    await screen.findByLabelText("Practitioner");
    expect(mocked.mock.calls.some((call) => call[1] === "/members/people?journal=true")).toBe(true);
    expect(mocked.mock.calls.some((call) => call[1] === "/members/people")).toBe(false); // not the list of every member
  });

  it("asks to choose someone for an old entry that has no practitioner on the list, and sends nothing for it", async () => {
    mount(entry({ practitioner_user_id: null, practitioner_name: "Former Colleague" }));
    const select = (await screen.findByLabelText("Practitioner")) as HTMLSelectElement;
    expect(select.options[0].text).toBe("Choose a practitioner"); // no confusing second copy of a name
    await userEvent.type(screen.getByLabelText(/Observations/), "x");
    answers.push(ok(entry({ body: "x", version: 2 })));
    await userEvent.click(screen.getByTestId("save-draft"));
    expect(calls()[0][2]).toMatchObject({ body: { body: "x" } });
  });
});

describe("when saving does not work", () => {
  it("a change made elsewhere is refused, said plainly, and nothing is shown as saved", async () => {
    answers.push(fail(409, { detail: { code: "stale_record", message: "stale", current_version: 3 } }));
    mount();
    await userEvent.type(screen.getByLabelText(/Observations/), "x");
    await userEvent.click(screen.getByTestId("save-draft"));
    expect((await screen.findByTestId("stale")).textContent).toContain("changed somewhere else");
    expect(status()).toBe("Unsaved changes");
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(router.refresh).toHaveBeenCalled();
  });

  it("a validation message from the backend appears at the control", async () => {
    answers.push(fail(422, { detail: [{ loc: ["body", "body"], msg: "String should have at most 20000 characters", type: "string_too_long" }] }));
    mount();
    await userEvent.type(screen.getByLabelText(/Observations/), "x");
    await userEvent.click(screen.getByTestId("save-draft"));
    expect(await screen.findByText(/at most 20000/)).toBeTruthy();
  });
});

describe("deleting a draft", () => {
  it("asks first, then deletes against the version it was opened on and goes back to the journal", async () => {
    answers.push(ok(undefined));
    mount(entry({ version: 4 }));
    await userEvent.click(screen.getByTestId("delete-draft"));
    expect(calls()).toHaveLength(0); // the first click only asks
    await userEvent.click(screen.getByTestId("delete-draft-confirm"));
    await waitFor(() => expect(calls()).toHaveLength(1));
    expect(calls()[0]).toEqual([ORG, `/horses/${HORSE}/journal/${ENTRY}`, { method: "DELETE", ifMatch: 4 }]);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/o/${ORG}/horses/${HORSE}/journal`));
  });

  it("stays on the entry and says why when the draft was changed or finalized elsewhere", async () => {
    answers.push(fail(409, { detail: { code: "journal_entry_final", message: "final" } }));
    mount();
    await userEvent.click(screen.getByTestId("delete-draft"));
    await userEvent.click(screen.getByTestId("delete-draft-confirm"));
    expect((await screen.findByRole("alert")).textContent).toContain("finalized elsewhere");
    expect(router.push).not.toHaveBeenCalled();
  });
});

describe("backing out of a question", () => {
  it("says where it goes: back to the draft for finalizing, and keeping the draft for deleting", async () => {
    mount();
    await userEvent.click(screen.getByTestId("finalize"));
    expect(screen.getByRole("button", { name: "No, back to the draft" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
    await userEvent.click(screen.getByTestId("finalize-keep"));
    expect(screen.getByTestId("finalize")).toBeTruthy(); // back where it was, nothing sent
    await userEvent.click(screen.getByTestId("delete-draft"));
    expect(screen.getByRole("button", { name: "No, keep the draft" })).toBeTruthy();
    expect(calls()).toHaveLength(0);
  });
});

describe("finalizing", () => {
  it("asks first, saves the unsaved changes, then finalizes with the new version and shows the frozen entry", async () => {
    answers.push(ok(entry({ body: "Done", version: 2 })), ok(entry({ body: "Done", version: 3, status: "final" })));
    mount();
    await userEvent.type(screen.getByLabelText(/Observations/), "Done");
    await userEvent.click(screen.getByTestId("finalize"));
    expect(calls()).toHaveLength(0); // the first click only asks
    await userEvent.click(screen.getByTestId("finalize-confirm"));
    await waitFor(() => expect(calls()).toHaveLength(2));
    expect(calls()[0][2]).toMatchObject({ method: "PATCH", ifMatch: 1 });
    expect(calls()[1]).toEqual([ORG, `/horses/${HORSE}/journal/${ENTRY}/finalize`, { method: "POST", ifMatch: 2 }]);
    await waitFor(() => expect(router.refresh).toHaveBeenCalled());
  });

  it("does not finalize when the save before it fails", async () => {
    answers.push(fail(409, { detail: { code: "stale_record", message: "stale", current_version: 3 } }));
    mount();
    await userEvent.type(screen.getByLabelText(/Observations/), "x");
    await userEvent.click(screen.getByTestId("finalize"));
    await userEvent.click(screen.getByTestId("finalize-confirm"));
    await screen.findByTestId("stale");
    expect(calls()).toHaveLength(1);
    expect(router.refresh).not.toHaveBeenCalled();
  });
});
