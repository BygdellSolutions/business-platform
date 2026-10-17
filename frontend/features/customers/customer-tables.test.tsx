import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ServiceList } from "@/components/history/ServiceList";
import type { BoughtLine, InvoiceStateOfOrder, ServiceRecord, TransactionSummary } from "@/lib/api/types";
import { CustomerBought } from "./CustomerBought";
import { CustomerOrders } from "./CustomerOrders";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const CUSTOMER = "11111111-1111-4111-8111-111111111111";
const TX = (n: number) => `22222222-2222-4222-8222-${String(n).padStart(12, "0")}`;

function order(number: number, overrides: Partial<TransactionSummary> = {}): TransactionSummary {
  return {
    id: TX(number), number, billing_customer_id: CUSTOMER, transaction_date: "2026-10-0" + (number % 9), status: "completed", currency: "SEK", line_count: 2, version: 1, header_version: 1,
    totals: { net_amount: "100.00", vat_amount: "25.00", gross_amount: "125.00" }, created_at: "", updated_at: "", created_by: null, updated_by: null,
    paid_at: null, payment_method: null, receipt_number_text: null, ...overrides,
  } as unknown as TransactionSummary;
}
const bought = {
  transaction_id: TX(1004), transaction_number: 1004, transaction_date: "2026-10-04", status: "completed", currency: "SEK", line_id: "l1", item_id: null, description: "Fly spray",
  unit: "pcs", quantity: "2.000", unit_price_ex_vat: "150.00", gross_amount: "375.00",
} as unknown as BoughtLine;
const service = {
  transaction_id: TX(1004), transaction_number: 1004, transaction_date: "2026-10-04", status: "completed", currency: "SEK", line_id: "l2", description: "Massage", quantity: "1.000",
  gross_amount: "850.00", performed_at: "2026-10-03T12:00:00Z", performed_by_name: "Tina", subject_type: "horse", subject_id: "h1", subject_label: "Kalle", notes: null,
} as unknown as ServiceRecord;

const headings = (testId: string) => within(screen.getByTestId(testId)).getAllByRole("columnheader").map((heading) => heading.textContent?.replace(/[▲▼↕]/g, "").trim());

describe("the customer's three tables", () => {
  it("start with the same three columns, in the same place, and end with the amount", () => {
    render(
      <>
        <CustomerOrders orgId={ORG} customerId={CUSTOMER} orders={[order(1004)]} invoices={[]} hasMore={false} />
        <CustomerBought orgId={ORG} lines={[bought]} />
        <ServiceList orgId={ORG} services={[service]} timeZone="Europe/Stockholm" title="Services billed to this customer" />
      </>,
    );
    const orders = headings("customer-orders");
    const lines = headings("customer-bought");
    const services = headings("service-list");
    for (const columns of [orders, lines, services]) expect(columns.slice(0, 3)).toEqual(["Order no.", "Date", "Status"]);
    expect(orders.at(-1)).toBe("Total");
    expect(lines.at(-1)).toBe("Amount");
    expect(services.at(-1)).toBe("Amount");
  });

  it("show the order number, date and status of every row the same way", () => {
    render(
      <>
        <CustomerOrders orgId={ORG} customerId={CUSTOMER} orders={[order(1004, { transaction_date: "2026-10-04" })]} invoices={[]} hasMore={false} />
        <CustomerBought orgId={ORG} lines={[bought]} />
        <ServiceList orgId={ORG} services={[service]} timeZone="Europe/Stockholm" title="Services billed to this customer" />
      </>,
    );
    for (const row of [screen.getByTestId("customer-order"), screen.getByTestId("bought-row"), screen.getByTestId("service-row")]) {
      expect(within(row).getByTestId("order-number").textContent).toBe("1004"); // the products table once showed nothing here
      expect(within(row).getByTestId("order-number").querySelector("a")?.getAttribute("href")).toBe(`/o/${ORG}/transactions/${TX(1004)}`);
      expect(within(row).getByTestId("order-status").textContent).toBe("Completed");
      expect(within(row).getByTestId("order-date").textContent).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
    expect(within(screen.getByTestId("service-row")).getByTestId("order-date").textContent).toBe("2026-10-03"); // a service's own day
  });
});

describe("the invoice / payment column of the orders", () => {
  it("says how each order stands: invoiced, paid directly at the counter, or not invoiced yet", () => {
    const invoices = [{ transaction_id: TX(1), state: "invoiced", invoice_id: "i1", number_text: "2001", invoices: [] }] as unknown as InvoiceStateOfOrder[];
    render(
      <CustomerOrders
        orgId={ORG}
        customerId={CUSTOMER}
        orders={[order(1), order(2, { paid_at: "2026-10-05T10:00:00Z", payment_method: "card", receipt_number_text: "R-7" }), order(3)]}
        invoices={invoices}
        hasMore={false}
      />,
    );
    const cells = screen.getAllByTestId("customer-order-invoice").map((cell) => cell.textContent);
    expect(cells[0]).toBe("Invoice 2001");
    expect(cells[1]).toBe("Paid directly · Card · receipt R-7"); // it used to say "Not invoiced yet"
    expect(cells[2]).toBe("Not invoiced yet");
  });
});
