import { describe, expect, it } from "vitest";

import { classify } from "@/features/invoices/failures";
import { normalizeError, type ApiError } from "@/lib/api/errors";

const conflict = (code: string, extra: Record<string, unknown> = {}): ApiError => normalizeError(409, { detail: { code, message: `msg ${code}`, ...extra } });

describe("classifying what the Invoicing API answered", () => {
  it("a stale record, with the version the server has now", () => {
    expect(classify(conflict("stale_record", { current_version: 9 }))).toEqual({ kind: "stale", message: "msg stale_record", currentVersion: 9 });
  });

  it("an invoice that is no longer a draft", () => {
    expect(classify(conflict("invoice_issued"))).toEqual({ kind: "issued", message: "msg invoice_issued" });
  });

  it("a draft that no longer matches its sources", () => {
    expect(classify(conflict("source_changed"))).toEqual({ kind: "sourceChanged", message: "msg source_changed" });
  });

  it.each(["transactions_not_completed", "mixed_customers", "currency_missing", "mixed_currencies", "already_invoiced"])("%s names the transactions concerned", (code) => {
    expect(classify(conflict(code, { transaction_ids: ["t1", "t2"] }))).toEqual({ kind: "ineligible", code, message: `msg ${code}`, transactionIds: ["t1", "t2"] });
  });

  it("an unknown conflict keeps the backend's message", () => {
    expect(classify(conflict("something_new"))).toEqual({ kind: "conflict", message: "msg something_new" });
    expect(classify(normalizeError(409, { detail: "plain text" }))).toEqual({ kind: "conflict", message: "plain text" });
  });

  it("validation keeps its field errors", () => {
    const failure = classify(normalizeError(422, { detail: [{ loc: ["body", "due_date"], msg: "bad", type: "x" }] }));
    expect(failure).toEqual({ kind: "validation", fieldErrors: { due_date: ["bad"] }, formErrors: [] });
  });

  it("a missing record, whether foreign or random, is simply gone", () => {
    expect(classify(normalizeError(404, { detail: "Not found" }))).toEqual({ kind: "gone" });
  });

  it("a network failure and a server error have an UNKNOWN outcome", () => {
    expect(classify({ kind: "network", status: 0, message: "offline" })).toEqual({ kind: "unconfirmed", message: "offline" });
    expect(classify(normalizeError(500, { detail: "Traceback" })).kind).toBe("unconfirmed");
    expect(classify(normalizeError(502, { detail: "Backend unavailable" })).kind).toBe("unconfirmed");
  });

  it("a refusal of the role, a missing or malformed If-Match are ordinary messages, not unknown outcomes", () => {
    expect(classify(normalizeError(403, { detail: "no" })).kind).toBe("other");
    expect(classify(normalizeError(428, { detail: "needs If-Match" })).kind).toBe("other");
    expect(classify(normalizeError(400, { detail: "Invalid If-Match header" })).kind).toBe("other");
  });
});
