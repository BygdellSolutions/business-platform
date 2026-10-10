import { describe, expect, it } from "vitest";

import { isInvoicePdfPath } from "@/lib/backend";

const ID = "00000000-0000-4000-8000-0000000000a1";

describe("the PDFs the BFF passes through", () => {
  it.each([`/api/invoices/${ID}/pdf`, `/api/invoices/credit-notes/${ID}/pdf`, `/api/invoices/receipts/${ID}/pdf`])("%s", (path) => {
    expect(isInvoicePdfPath(path)).toBe(true);
  });

  it.each([`/api/invoices/receipts/${ID}`, `/api/invoices/receipts/x/pdf`, `/api/transactions/${ID}/pdf`, `/api/invoices/other/${ID}/pdf`])("not %s", (path) => {
    expect(isInvoicePdfPath(path)).toBe(false);
  });
});
