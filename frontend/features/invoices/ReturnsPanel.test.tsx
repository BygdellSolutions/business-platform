import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { ReturnsPanel } from "@/features/invoices/ReturnsPanel";
import { INVOICE_ID, LINE_1, ORG_A, calls, installBackend, invoice, issued, line, ok, resetServer, router } from "@/features/invoices/testing";
import type { Invoice, InvoiceReturn } from "@/lib/api/types";
import type { QuantityString } from "@/lib/decimal";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const q = (value: string) => value as QuantityString;
const CASE: InvoiceReturn = {
  id: "rrrrrrrr-rrrr-4rrr-8rrr-rrrrrrrrrrrr",
  state: "requested",
  reason: "Wrong size",
  follow_up_on: "2026-10-09",
  rejection_reason: null,
  credit_note_id: null,
  created_at: "2026-10-02T08:00:00Z",
  lines: [{ invoice_line_id: LINE_1, description: "Horse massage", unit: "session", quantity: q("1.000"), returned_to_stock: false }],
  events: [{ kind: "opened", note: "Wrong size", created_at: "2026-10-02T08:00:00Z", created_by_name: "Anna" }],
};

function show(current: Invoice, canHandle = true) {
  render(
    <OrgScope orgId={ORG_A}>
      <ReturnsPanel invoice={current} canHandle={canHandle} today="2026-10-10" timeZone="Europe/Stockholm" />
    </OrgScope>,
  );
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(invoice());
  installBackend(() => ok(issued()));
});

describe("ReturnsPanel", () => {
  it("shows nothing on a draft", () => {
    show(invoice());
    expect(screen.queryByTestId("returns")).toBeNull();
  });

  it("opens a return with the quantities typed, a reason and a follow-up a week from today", async () => {
    show(issued({ lines: [line()] }));
    await userEvent.click(screen.getByTestId("open-return"));
    fireEvent.change(screen.getByLabelText("Quantity to return of Horse massage"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Reason", { exact: false }), { target: { value: "Wrong size" } });
    await userEvent.click(screen.getByTestId("create-return"));

    await waitFor(() => expect(calls()).toHaveLength(1));
    expect(calls()[0]).toMatchObject({
      method: "POST",
      path: `/invoices/${INVOICE_ID}/returns`,
      body: { reason: "Wrong size", follow_up_on: "2026-10-17", lines: [{ invoice_line_id: LINE_1, quantity: "1" }] },
    });
  });

  it("marks a due follow-up, and approving opens the credit form for the case", async () => {
    show(issued({ returns: [CASE], open_returns: 1 }));
    expect(screen.getByTestId("return-follow-up").textContent).toContain("(due)");
    await userEvent.click(screen.getByTestId("return-approve"));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`?credit_return=${CASE.id}#credit-notes`));
    expect(calls()[0]).toMatchObject({ method: "POST", path: `/invoices/${INVOICE_ID}/returns/${CASE.id}/approve` });
  });

  it("shows a closed case without actions, and nothing to those who cannot handle returns", () => {
    show(issued({ returns: [{ ...CASE, state: "rejected", rejection_reason: "Used" }] }), false);
    expect(screen.getByTestId("return-state").textContent).toBe("Rejected");
    expect(screen.getByTestId("return-rejection").textContent).toContain("Used");
    expect(screen.queryByTestId("return-approve")).toBeNull();
    expect(screen.queryByTestId("open-return")).toBeNull();
  });
});
