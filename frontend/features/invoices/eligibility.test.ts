import { describe, expect, it } from "vitest";

import { compatibility, createBody, toggle, without } from "@/features/invoices/eligibility";
import { eligible } from "@/features/invoices/testing";

const A = eligible("a1");
const B = eligible("b2");
const OTHER_CUSTOMER = eligible("c3", { billing_customer_id: "99999999-9999-4999-8999-999999999999", billing_customer: { id: "99999999-9999-4999-8999-999999999999", name: "Anna", active: true } });
const OTHER_CURRENCY = eligible("d4", { currency: "EUR" });

describe("compatibility", () => {
  it("anything may start a selection", () => {
    for (const row of [A, OTHER_CUSTOMER, OTHER_CURRENCY]) expect(compatibility([], row)).toBe("ok");
  });

  it("the same customer and currency fit", () => {
    expect(compatibility([A], B)).toBe("ok");
  });

  it("another customer does not, and another currency does not", () => {
    expect(compatibility([A], OTHER_CUSTOMER)).toBe("different_customer");
    expect(compatibility([A], OTHER_CURRENCY)).toBe("different_currency");
  });

  it("a different customer with a different currency is reported as the customer (the first reason)", () => {
    expect(compatibility([A], eligible("e5", { ...OTHER_CUSTOMER, currency: "EUR" }))).toBe("different_customer");
  });

  it("the customer is compared by id, never by name (two customers may share a name)", () => {
    const sameName = eligible("f6", { billing_customer_id: "88888888-8888-4888-8888-888888888888", billing_customer: { id: "88888888-8888-4888-8888-888888888888", name: A.billing_customer.name, active: true } });
    expect(compatibility([A], sameName)).toBe("different_customer");
  });

  it("currencies are compared as codes, exactly", () => {
    expect(compatibility([A], eligible("g7", { currency: "sek" }))).toBe("different_currency");
  });
});

describe("toggle", () => {
  it("adds a compatible row and removes a selected one", () => {
    expect(toggle([], A)).toEqual([A]);
    expect(toggle([A], B)).toEqual([A, B]);
    expect(toggle([A, B], A)).toEqual([B]);
  });

  it("never adds an incompatible row", () => {
    expect(toggle([A], OTHER_CUSTOMER)).toEqual([A]);
    expect(toggle([A], OTHER_CURRENCY)).toEqual([A]);
  });

  it("does not modify the selection it was given", () => {
    const selection = [A];
    toggle(selection, B);
    expect(selection).toEqual([A]);
  });
});

describe("without", () => {
  it("forgets exactly the named transactions", () => {
    expect(without([A, B], ["b2"])).toEqual([A]);
    expect(without([A, B], ["zzz"])).toEqual([A, B]);
    expect(without([A, B], [])).toEqual([A, B]);
  });
});

describe("createBody", () => {
  it("is the ids in selection order and nothing else when the header is blank", () => {
    expect(createBody([A, B], { invoiceDate: "", dueDate: "", description: "  " })).toEqual({ transaction_ids: ["a1", "b2"] });
  });

  it("adds the approved header fields that were filled in", () => {
    expect(createBody([A], { invoiceDate: "2026-10-01", dueDate: "2026-10-31", description: "Text" })).toEqual({
      transaction_ids: ["a1"],
      invoice_date: "2026-10-01",
      due_date: "2026-10-31",
      description: "Text",
    });
  });

  it("carries no customer, currency, organization or amount, whatever the rows hold", () => {
    const body = createBody([A, OTHER_CUSTOMER], { invoiceDate: "", dueDate: "", description: "" }) as unknown as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(["transaction_ids"]);
  });
});
