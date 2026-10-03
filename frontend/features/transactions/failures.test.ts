import { describe, expect, it } from "vitest";

import { networkError, normalizeError } from "@/lib/api/errors";
import { classify, describeProblem } from "@/features/transactions/failures";

describe("classify", () => {
  it("422 is validation, with the field errors", () => {
    const failure = classify(normalizeError(422, { detail: [{ loc: ["body", "quantity"], msg: "Input should be greater than 0", type: "x" }] }));
    expect(failure).toEqual({ kind: "validation", fieldErrors: { quantity: ["Input should be greater than 0"] }, formErrors: [] });
  });

  it("409 stale_record is stale, with the current version", () => {
    const failure = classify(normalizeError(409, { detail: { code: "stale_record", message: "Changed elsewhere", current_version: 5 } }));
    expect(failure).toEqual({ kind: "stale", message: "Changed elsewhere", currentVersion: 5 });
  });

  it("409 validation_failed is problems, kept whole", () => {
    const problem = { code: "custom_field.required", message: "is required", entity_type: "transaction_line", entity_id: "L1", field: "owner", label: "Owner" };
    const failure = classify(normalizeError(409, { detail: { code: "validation_failed", message: "Blocked: 1 problem(s)", problems: [problem] } }));
    expect(failure).toEqual({ kind: "problems", message: "Blocked: 1 problem(s)", problems: [problem] });
  });

  it.each(["A completed transaction cannot be changed; reopen it first", "A transaction needs at least one line to be completed"])(
    "any other 409 is a conflict carrying the backend's own words: %s",
    (message) => {
      expect(classify(normalizeError(409, { detail: message }))).toEqual({ kind: "conflict", message });
    },
  );

  it("404 is gone, and says nothing about why", () => {
    expect(classify(normalizeError(404, { detail: "Not found" }))).toEqual({ kind: "gone" });
    expect(classify(normalizeError(404, { detail: "belongs to another organization" }))).toEqual({ kind: "gone" });
  });

  it.each([
    [normalizeError(403, { detail: "Your role does not allow this." }), "Your role does not allow this."],
    [normalizeError(428, { detail: "This change must say which version it is based on" }), "This change must say which version it is based on"],
    [normalizeError(500, { detail: "Traceback" }), "The server could not complete the request. Try again."],
    [networkError(), "Could not reach the server. Check your connection and try again."],
  ])("everything else is other: %#", (error, message) => {
    expect(classify(error)).toEqual({ kind: "other", message });
  });
});

describe("describeProblem", () => {
  const lines = [{ id: "L1" }, { id: "L2" }];
  const base = { code: "custom_field.required", message: "is required", entity_id: "L2", field: "owner", label: "Owner" };

  it("says which line, by position, and which field", () => {
    expect(describeProblem({ ...base, entity_type: "transaction_line" }, lines)).toBe("Line 2 · Owner: is required");
  });

  it("falls back to the field key without a label, and to no field at all", () => {
    expect(describeProblem({ ...base, entity_type: "transaction_line", label: null }, lines)).toBe("Line 2 · owner: is required");
    expect(describeProblem({ ...base, entity_type: "transaction_line", label: null, field: null }, lines)).toBe("Line 2: is required");
  });

  it("names the transaction itself", () => {
    expect(describeProblem({ ...base, entity_type: "transaction", entity_id: "T1" }, lines)).toBe("Transaction · Owner: is required");
  });

  it("does not guess about a line it cannot find, or a record type it does not know", () => {
    expect(describeProblem({ ...base, entity_type: "transaction_line", entity_id: "gone" }, lines)).toBe("Record · Owner: is required");
    expect(describeProblem({ ...base, entity_type: "horse" }, lines)).toBe("Record · Owner: is required");
  });
});
