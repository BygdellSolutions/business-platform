import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InvoiceDocument } from "@/features/invoices/InvoiceDocument";
import { InvoiceStatusBadge } from "@/features/invoices/InvoiceStatusBadge";
import { CUSTOMER_ID, INVOICE_ID, ORG_A, TX_1, TX_2, invoice, issued, line, party, snapshot } from "@/features/invoices/testing";
import type { MoneyString, PercentString, QuantityString } from "@/lib/decimal";

// If the document tried to look anything up, these would be called (and the tests below say they are not).
vi.mock("@/lib/api/client", () => ({ apiFetch: vi.fn() }));
import { apiFetch } from "@/lib/api/client";

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  // A "live" world that DISAGREES with every invoice below. If a screen read it, it would show.
  vi.mocked(apiFetch).mockImplementation((async (_org: string, path: string) => {
    if (path.startsWith("/customers")) return { ok: true, status: 200, data: { id: CUSTOMER_ID, name: "LIVE customer name", city: "Live City", vat_number: "LIVE-VAT" } };
    if (path.startsWith("/items")) return { ok: true, status: 200, data: [{ name: "LIVE item name", price_ex_vat: "1.00" }] };
    if (path.startsWith("/custom-fields")) return { ok: true, status: 200, data: [{ label: "LIVE field label" }] };
    if (path.startsWith("/transactions")) return { ok: true, status: 200, data: { transaction_date: "1999-01-01", billing_customer: { name: "LIVE customer name" } } };
    return { ok: true, status: 200, data: null };
  }) as typeof apiFetch);
});

const text = (testId: string) => screen.getByTestId(testId).textContent;
const money = (value: string) => value as MoneyString;

describe("the document shows the server's strings, exactly (no arithmetic)", () => {
  // Nothing here adds up: every figure disagrees with the others. A UI that recomputed anything
  // (a total, a line amount, a VAT row, a gross) would show a different number somewhere.
  const inconsistent = invoice({
    net_amount: money("7.77"),
    vat_amount: money("8.88"),
    gross_amount: money("1.11"),
    lines: [
      line({ net_amount: money("1.00"), vat_amount: money("2.00"), gross_amount: money("99.99"), quantity: "3.000" as QuantityString, unit_price_ex_vat: money("50.00"), vat_rate: "25.00" as PercentString }),
      line({ id: "55555555-5555-4555-8555-555555555552", position: 2, net_amount: money("0.10"), vat_amount: money("0.1"), gross_amount: money("9999999999.99"), quantity: "0.001" as QuantityString }),
    ],
    vat_breakdown: [
      { vat_rate: "25.00" as PercentString, net_amount: money("3.33"), vat_amount: money("4.44") },
      { vat_rate: "6.00" as PercentString, net_amount: money("5.55"), vat_amount: money("6.66") },
    ],
  });

  it("header totals are the stored ones, not the sum of the lines", () => {
    render(<InvoiceDocument invoice={inconsistent} orgId={ORG_A} />);
    expect([text("total-net"), text("total-vat"), text("total-gross")]).toEqual(["7.77", "8.88", "1.11"]);
  });

  it("line amounts, quantities, prices and rates are shown as stored (trailing zeros, 0.1, a huge amount)", () => {
    render(<InvoiceDocument invoice={inconsistent} orgId={ORG_A} />);
    const [first, second] = screen.getAllByTestId("invoice-line");
    expect([within(first).getByTestId("line-net").textContent, within(first).getByTestId("line-vat").textContent, within(first).getByTestId("line-gross").textContent]).toEqual(["1.00", "2.00", "99.99"]);
    expect([within(first).getByTestId("line-quantity").textContent, within(first).getByTestId("line-unit-price").textContent, within(first).getByTestId("line-vat-rate").textContent]).toEqual(["3.000", "50.00", "25.00"]);
    expect([within(second).getByTestId("line-net").textContent, within(second).getByTestId("line-vat").textContent, within(second).getByTestId("line-gross").textContent]).toEqual(["0.10", "0.1", "9999999999.99"]);
  });

  it("the VAT breakdown is the stored rows, in the stored order, not regrouped from the lines", () => {
    render(<InvoiceDocument invoice={inconsistent} orgId={ORG_A} />);
    const rows = screen.getAllByTestId("vat-row").map((row) => Array.from(row.querySelectorAll("td")).map((cell) => cell.textContent));
    expect(rows).toEqual([
      ["25.00", "3.33", "4.44"],
      ["6.00", "5.55", "6.66"],
    ]);
  });

  it("the document contains none of the numbers a recalculation would have produced", () => {
    render(<InvoiceDocument invoice={inconsistent} orgId={ORG_A} />);
    const body = screen.getByTestId("invoice-document").textContent ?? "";
    for (const wouldBe of ["1.10", "3.10", "12.77", "100.99", "8.00", "17.76", "9999999999.99100"]) expect(body).not.toContain(wouldBe);
  });

  it("shows the currency the invoice carries", () => {
    render(<InvoiceDocument invoice={invoice({ currency: "EUR" })} orgId={ORG_A} />);
    expect(text("invoice-currency")).toBe("EUR");
    expect(screen.getByText("Totals (EUR)")).toBeInTheDocument();
  });
});

describe("the number is the server's", () => {
  it("a draft has no number and says so", () => {
    render(<InvoiceDocument invoice={invoice()} orgId={ORG_A} />);
    expect(text("invoice-heading")).toBe("Draft invoice (no number yet)");
    expect(screen.queryByTestId("invoice-number")).toBeNull();
    expect(text("invoice-status")).toBe("Draft");
  });

  it("an issued invoice shows the stored number text exactly, whatever it looks like", () => {
    render(<InvoiceDocument invoice={issued({ number: 7, number_text: "INV/2026-007" })} orgId={ORG_A} />);
    expect(text("invoice-heading")).toBe("Invoice INV/2026-007");
    expect(text("invoice-number")).toBe("INV/2026-007");
    expect(text("invoice-status")).toBe("Issued");
  });

  it("the text is used, not the integer (they may differ)", () => {
    render(<InvoiceDocument invoice={issued({ number: 7, number_text: "A-7" })} orgId={ORG_A} />);
    expect(text("invoice-number")).toBe("A-7");
    expect(screen.queryByText("7")).toBeNull();
  });

  it("shows the stored dates, description, version and when it was issued", () => {
    render(<InvoiceDocument invoice={issued({ invoice_date: "2026-10-01", due_date: null, description: null })} orgId={ORG_A} />);
    expect([text("invoice-date"), text("invoice-due-date"), text("invoice-description"), text("invoice-version")]).toEqual(["2026-10-01", "—", "—", "4"]);
    expect(text("invoice-issued-at")).toBe("2026-10-02T09:00:00Z");
  });
});

describe("the snapshot wins over live data (the live world deliberately disagrees)", () => {
  const doc = invoice({
    customer_name: "Snapshot Club",
    customer_snapshot: party({ name: "Snapshot Club", city: "Snapshot City", vat_number: "SNAP-VAT" }),
    transactions: [{ transaction_id: TX_1, position: 1, transaction_date: "2026-09-30", source_version: 1, fields: [snapshot({ label: "Stored header label", display: "stored header value" })] }],
    lines: [line({ description: "Stored description", fields: [snapshot({ key: "owner", label: "Stored owner label", field_type: "reference", display: "Stored Owner Name", value: "some-id" })] })],
  });

  it("every word of the document is the invoice's own", () => {
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    const body = screen.getByTestId("invoice-document").textContent ?? "";
    for (const stored of ["Snapshot Club", "Snapshot City", "SNAP-VAT", "Stored description", "Stored owner label", "Stored Owner Name", "Stored header label", "stored header value", "2026-09-30"]) {
      expect(body).toContain(stored);
    }
    for (const live of ["LIVE", "Live City", "1999-01-01"]) expect(body).not.toContain(live);
  });

  it("nothing is fetched to fill the document in", () => {
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("the customer block comes from the snapshot", () => {
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    const customer = within(screen.getByTestId("party-customer"));
    expect(customer.getByTestId("party-customer-name")).toHaveTextContent("Snapshot Club");
    expect(customer.getByText(/SNAP-VAT/)).toBeInTheDocument();
    expect(customer.queryByText(/LIVE/)).toBeNull();
  });

  it("the issuer block shows the legal name stored on the invoice", () => {
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    expect(screen.getByTestId("party-issuer-name")).toHaveTextContent("Fredrik Horse Therapy AB");
  });

  it("source links are secondary audit navigation, labelled with the stored date, never replacing snapshot text", () => {
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    const sources = within(screen.getByTestId("sources"));
    expect(sources.getByText(/navigation and audit only/)).toBeInTheDocument();
    const link = sources.getByTestId("source-link");
    expect(link).toHaveTextContent("Order of 2026-09-30");
    expect(link).toHaveAttribute("href", `/o/${ORG_A}/transactions/${TX_1}`);
    // The links are the ONLY links in the document, and no link carries customer or item text.
    expect(within(screen.getByTestId("invoice-document")).getAllByRole("link")).toHaveLength(1);
  });

  it("the source section is outside the document body it supports (it is not part of a line or a total)", () => {
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    expect(within(screen.getByTestId("invoice-lines")).queryByTestId("source-link")).toBeNull();
    expect(within(screen.getByTestId("invoice-totals")).queryByTestId("source-link")).toBeNull();
  });

  it("several sources are listed, each with its own stored fields", () => {
    const two = invoice({
      transactions: [
        { transaction_id: TX_1, position: 1, transaction_date: "2026-09-30", source_version: 1, fields: [snapshot({ label: "PO", display: "PO-1" })] },
        { transaction_id: TX_2, position: 2, transaction_date: "2026-10-02", source_version: 1, fields: [] },
      ],
    });
    render(<InvoiceDocument invoice={two} orgId={ORG_A} />);
    const rows = screen.getAllByTestId("source");
    expect(rows.map((row) => within(row).getByTestId("source-link").textContent)).toEqual(["Order of 2026-09-30", "Order of 2026-10-02"]);
    expect(within(rows[0]).getByText("PO-1")).toBeInTheDocument();
    expect(within(rows[1]).queryByTestId("transaction-fields")).toBeNull();
  });
});

describe("custom-field snapshots in the document", () => {
  it("shows line-level and transaction-level snapshots read-only, from stored text", () => {
    const doc = invoice({
      transactions: [{ transaction_id: TX_1, position: 1, transaction_date: "2026-09-30", source_version: 1, fields: [snapshot({ label: "PO number", display: "PO-17" })] }],
      lines: [line({ fields: [snapshot({ label: "Kind", field_type: "select", display: "Therapy", value: "opt" }), snapshot({ key: "ok", label: "Checked", field_type: "boolean", value: true, display: "true", definition_id: "d-2" })] })],
    });
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    expect(within(screen.getByTestId("transaction-fields")).getByText("PO-17")).toBeInTheDocument();
    const fields = within(screen.getByTestId("line-fields"));
    expect(fields.getByText("Therapy")).toBeInTheDocument();
    expect(fields.getByText("Yes")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("a reference that was already gone when recorded is shown from the stored state", () => {
    const doc = invoice({ lines: [line({ fields: [snapshot({ field_type: "reference", display: null, missing: true, value: "gone", label: "Owner" })] })] });
    render(<InvoiceDocument invoice={doc} orgId={ORG_A} />);
    expect(screen.getByText("(no longer existed)")).toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("works with nothing else available: no definitions, no records, no client", () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("the backend is down"));
    const doc = invoice({ lines: [line({ fields: [snapshot({ label: "Remark", display: "kept" })] })] });
    expect(() => render(<InvoiceDocument invoice={doc} orgId={ORG_A} />)).not.toThrow();
    expect(screen.getByText("kept")).toBeInTheDocument();
  });
});

describe("shape of the page", () => {
  it("an invoice with no lines says so", () => {
    render(<InvoiceDocument invoice={invoice({ lines: [], vat_breakdown: [] })} orgId={ORG_A} />);
    expect(screen.getByTestId("no-lines")).toBeInTheDocument();
    expect(screen.queryByTestId("vat-breakdown")).toBeNull();
  });

  it("has the id the page was asked for only as an anchor for tests, not as text", () => {
    render(<InvoiceDocument invoice={invoice()} orgId={ORG_A} />);
    expect(screen.getByTestId("invoice-document").textContent).not.toContain(INVOICE_ID);
  });
});


describe("the status says how far an issued invoice is paid", () => {
  it.each([
    ["unpaid", "Unpaid"],
    ["partially_paid", "Partially paid"],
    ["paid", "Paid"],
  ] as const)("%s reads %s", (paymentStatus, label) => {
    render(<InvoiceStatusBadge status="issued" paymentStatus={paymentStatus} />);
    expect(screen.getByTestId("invoice-status")).toHaveTextContent(label);
  });

  it("a draft is a draft whatever else is said", () => {
    render(<InvoiceStatusBadge status="draft" paymentStatus={null} />);
    expect(screen.getByTestId("invoice-status")).toHaveTextContent("Draft");
  });
});
