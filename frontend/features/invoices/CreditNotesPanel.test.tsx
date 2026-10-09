import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { CreditNotesPanel } from "@/features/invoices/CreditNotesPanel";
import { INVOICE_ID, LINE_1, LINE_2, ORG_A, calls, fail, installBackend, invoice, issued, line, ok, resetServer, router } from "@/features/invoices/testing";
import type { CreditNoteSummary, Invoice } from "@/lib/api/types";
import type { MoneyString, QuantityString } from "@/lib/decimal";

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn(), apiDownloadPdf: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

const q = (value: string) => value as QuantityString;
const NOTE: CreditNoteSummary = {
  id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  number_text: "8",
  credit_date: "2026-10-10",
  reason: "Returned",
  currency: "SEK",
  net_amount: "850.00" as MoneyString,
  vat_amount: "212.50" as MoneyString,
  gross_amount: "1062.50" as MoneyString,
  issued_at: "2026-10-10T08:00:00Z",
  issued_by_name: "Anna",
};

function show(current: Invoice, canCredit = true) {
  render(
    <OrgScope orgId={ORG_A}>
      <CreditNotesPanel invoice={current} canCredit={canCredit} timeZone="Europe/Stockholm" />
    </OrgScope>,
  );
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  resetServer(invoice());
  installBackend(() => ok(NOTE, 201));
});

describe("CreditNotesPanel", () => {
  it("shows nothing on a draft", () => {
    show(invoice());
    expect(screen.queryByTestId("credit-notes")).toBeNull();
  });

  it("credits all that is left of every line with a reason, and ticks returned to stock only where stock can come back", async () => {
    show(
      issued({
        lines: [
          line({ quantity: q("3.000"), credited_quantity: q("1.000"), creditable_quantity: q("2.000"), stock_returnable: q("2.000") }),
          line({ id: LINE_2, position: 2, description: "Travel", creditable_quantity: q("7.001") }),
        ],
      }),
    );
    await userEvent.click(screen.getByTestId("open-credit"));
    expect(screen.getAllByTestId("credit-returned")).toHaveLength(1);
    await userEvent.click(screen.getByTestId("credit-all"));
    await userEvent.click(screen.getByTestId("credit-returned"));
    fireEvent.change(screen.getByLabelText("Reason", { exact: false }), { target: { value: "  Came back  " } });
    await userEvent.click(screen.getByTestId("create-credit-note"));

    await waitFor(() => expect(calls()).toHaveLength(1));
    expect(calls()[0]).toMatchObject({
      method: "POST",
      path: `/invoices/${INVOICE_ID}/credit-notes`,
      body: {
        reason: "Came back",
        lines: [
          { invoice_line_id: LINE_1, quantity: "2.000", returned_to_stock: true },
          { invoice_line_id: LINE_2, quantity: "7.001", returned_to_stock: false },
        ],
      },
    });
    expect(router.refresh).toHaveBeenCalled();
    expect(screen.getByTestId("credit-created").textContent).toContain("Credit note 8 created");
  });

  it("sends only the lines given a quantity and shows the backend's refusal on its line", async () => {
    installBackend(() => fail(422, { detail: [{ loc: ["body", "lines", 0, "quantity"], msg: "Only 1 of this line can still be credited", type: "credit.too_much" }] }));
    show(issued({ lines: [line(), line({ id: LINE_2, position: 2, description: "Travel" })] }));
    await userEvent.click(screen.getByTestId("open-credit"));
    fireEvent.change(screen.getByLabelText("Credit quantity of Travel"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Reason", { exact: false }), { target: { value: "Wrong distance" } });
    await userEvent.click(screen.getByTestId("create-credit-note"));

    await waitFor(() => expect(screen.getByTestId("credit-line-error").textContent).toContain("Only 1 of this line"));
    expect((calls()[0].body as { lines: unknown[] }).lines).toEqual([{ invoice_line_id: LINE_2, quantity: "5", returned_to_stock: false }]);
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("lists credit notes with their PDF for every member, and offers no form to those who cannot credit", () => {
    show(issued({ credit_notes: [NOTE], lines: [line({ credited_quantity: q("1.000"), creditable_quantity: q("0.000") })] }), false);
    expect(screen.getByTestId("credit-note-number").textContent).toBe("8");
    expect(screen.getByTestId("credit-note-gross").textContent).toContain("1062.50");
    expect(screen.getByTestId("download-pdf").textContent).toBe("PDF");
    expect(screen.queryByTestId("open-credit")).toBeNull();
  });

  it("says when everything is credited", () => {
    show(issued({ credit_notes: [NOTE], lines: [line({ credited_quantity: q("1.000"), creditable_quantity: q("0.000") })] }));
    expect(screen.queryByTestId("open-credit")).toBeNull();
    expect(screen.getByText("Everything on this invoice is credited.")).toBeTruthy();
  });
});
