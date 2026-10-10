import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import type { HistoryEvent } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/timestamps";

const OLLE = { id: "u1", name: "Olle Owner" };

function event(overrides: Partial<HistoryEvent>): HistoryEvent {
  return { id: 1, occurred_at: "2026-10-07T22:30:00Z", actor: OLLE, entity_type: "customer", entity_id: "c1", action: "updated", changes: {}, ...overrides };
}

describe("formatTimestamp", () => {
  it("shows the stored instant in the organization's time zone", () => {
    expect(formatTimestamp("2026-10-07T22:30:00Z", "Europe/Stockholm")).toBe("2026-10-08 00:30");
  });

  it("says UTC when the organization has no zone", () => {
    expect(formatTimestamp("2026-10-07T22:30:00Z", null)).toBe("2026-10-07 22:30 UTC");
  });
});

describe("RecordHistory", () => {
  it("lists who changed what, when, with the old and the new value", () => {
    render(
      <RecordHistory
        entityType="customer"
        timeZone="Europe/Stockholm"
        data={{ history: { events: [event({ changes: { name: { from: "Anna", to: "Anna Andersson" }, active: { from: true, to: false } } })], people: [OLLE] }, names: {} }}
      />,
    );
    const entry = screen.getByTestId("history-event");
    expect(entry).toHaveTextContent("Changed");
    expect(entry).toHaveTextContent("2026-10-08 00:30 · Olle Owner");
    const changes = within(entry).getAllByTestId("history-change").map((li) => li.textContent);
    expect(changes).toEqual(["Name: Anna → Anna Andersson", "Active: Yes → No"]);
  });

  it("names referenced customers instead of showing ids, and says when one no longer exists", () => {
    render(
      <RecordHistory
        entityType="horse"
        timeZone={null}
        data={{ history: { events: [event({ entity_type: "horse", changes: { owner_customer_id: { from: "c-old", to: "c-new" } } })], people: [OLLE] }, names: { "c-new": "Umeå HK" } }}
      />,
    );
    expect(screen.getByTestId("history-change")).toHaveTextContent("Owner: a record that no longer exists → Umeå HK");
  });

  it("marks entries about a part of the record (a line of a transaction) and uses custom field labels as recorded", () => {
    render(
      <RecordHistory
        entityType="transaction"
        timeZone={null}
        data={{
          history: {
            events: [
              event({ id: 2, entity_type: "transaction_line", action: "created", changes: { description: { from: null, to: "Massage" } } }),
              event({ id: 1, entity_type: "transaction", action: "fields_updated", changes: { memo: { label: "Memo", from: null, to: "hello" } } }),
            ],
            people: [OLLE],
          },
          names: {},
        }}
      />,
    );
    const [line, fields] = screen.getAllByTestId("history-event");
    expect(line).toHaveTextContent("Line · Created");
    expect(fields).toHaveTextContent("Custom fields changed");
    expect(within(fields).getByTestId("history-change")).toHaveTextContent("Memo: — → hello");
  });

  it("says when nothing was recorded yet", () => {
    render(<RecordHistory entityType="item" timeZone={null} data={{ history: { events: [], people: [] }, names: {} }} />);
    expect(screen.getByTestId("history-empty")).toBeInTheDocument();
  });
});

describe("RecordMeta", () => {
  it("names the creator and the last editor, and never guesses an author that was not recorded", () => {
    render(
      <RecordMeta
        record={{ created_at: "2026-10-01T08:00:00Z", updated_at: "2026-10-07T22:30:00Z", created_by: null, updated_by: "u1" }}
        people={[OLLE]}
        timeZone="Europe/Stockholm"
      />,
    );
    expect(screen.getByTestId("created-by")).toHaveTextContent("not recorded");
    expect(screen.getByTestId("updated-by")).toHaveTextContent("Olle Owner");
    expect(screen.getByTestId("record-meta")).toHaveTextContent("Last changed 2026-10-08 00:30");
  });
});
