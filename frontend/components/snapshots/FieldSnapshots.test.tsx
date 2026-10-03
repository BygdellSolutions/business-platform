import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FieldSnapshots, GONE, presentSnapshot } from "@/components/snapshots/FieldSnapshots";
import type { FieldSnapshot } from "@/lib/api/types";

function field(overrides: Partial<FieldSnapshot>): FieldSnapshot {
  return { key: "k", label: "Label", field_type: "text", value: "stored", display: "stored", missing: false, position: 10, definition_id: "d-1", ...overrides };
}

describe("FieldSnapshots", () => {
  it("renders nothing for no fields", () => {
    const { container } = render(<FieldSnapshots fields={[]} label="Fields" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows each stored label and text, in the stored order", () => {
    render(
      <FieldSnapshots
        label="Fields"
        fields={[field({ key: "b", label: "Second shown first", display: "B", definition_id: "d-b" }), field({ key: "a", label: "A label", display: "A", definition_id: "d-a" })]}
      />,
    );
    const rows = screen.getAllByTestId("snapshot-field");
    expect(rows.map((row) => row.textContent)).toEqual(["Second shown firstB", "A labelA"]);
  });

  it.each([
    [field({ field_type: "text", value: "Handle with care", display: "Handle with care" }), "Handle with care"],
    [field({ field_type: "number", value: "0.10", display: "0.1" }), "0.1"], // the stored display, as it was rendered
    [field({ field_type: "date", value: "2026-10-01", display: "2026-10-01" }), "2026-10-01"],
    [field({ field_type: "boolean", value: true, display: "true" }), "Yes"],
    [field({ field_type: "boolean", value: false, display: "false" }), "No"],
    [field({ field_type: "select", value: "opt-1", display: "Therapy" }), "Therapy"],
    [field({ field_type: "reference", value: "some-id", display: "Anna Andersson" }), "Anna Andersson"],
  ])("presents %#", (snapshot, text) => {
    expect(presentSnapshot(snapshot)).toBe(text);
  });

  it("shows a reference that was already gone when it was recorded as such, from the stored state alone", () => {
    render(<FieldSnapshots label="Fields" fields={[field({ field_type: "reference", value: "gone-id", display: null, missing: true })]} />);
    expect(screen.getByTestId("snapshot-field")).toHaveAttribute("data-missing", "true");
    expect(within(screen.getByTestId("snapshot-field")).getByText(GONE)).toBeInTheDocument();
    expect(screen.queryByText("gone-id")).toBeNull(); // an id is never shown as text
  });

  it("never shows an id for a select or reference, only the stored text", () => {
    render(<FieldSnapshots label="Fields" fields={[field({ field_type: "select", value: "00000000-0000-4000-8000-0000000000aa", display: "Check" })]} />);
    expect(screen.getByText("Check")).toBeInTheDocument();
    expect(screen.queryByText(/0000000000aa/)).toBeNull();
  });

  it("needs nothing but the snapshot: no providers, no mocks, no client", () => {
    // If it tried to fetch or read a context it would throw here.
    expect(() => render(<FieldSnapshots label="Fields" fields={[field({ field_type: "reference", display: "Whatever" })]} />)).not.toThrow();
  });

  it("is read-only: no input, button or link", () => {
    render(<FieldSnapshots label="Fields" fields={[field({}), field({ field_type: "boolean", value: true, definition_id: "d-2", key: "b" })]} />);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("treats domain-looking keys and labels as plain text (no special cases)", () => {
    render(
      <FieldSnapshots
        label="Fields"
        fields={[
          field({ key: "owner", label: "Owner", field_type: "reference", display: "Anna", definition_id: "d-o" }),
          field({ key: "horse", label: "Horse", field_type: "reference", display: "Kalle", definition_id: "d-h" }),
        ]}
      />,
    );
    expect(screen.getAllByTestId("snapshot-field").map((row) => row.textContent)).toEqual(["OwnerAnna", "HorseKalle"]);
  });
});
