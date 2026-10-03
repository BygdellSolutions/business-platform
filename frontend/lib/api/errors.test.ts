import { describe, expect, it } from "vitest";

import { fieldPath, networkError, normalizeError } from "@/lib/api/errors";

// Payload shapes copied from what FastAPI actually returns.
const NOT_AUTHENTICATED = { detail: "Not authenticated" };
const ROLE = { detail: "Your role in this organization does not allow this action" };
const NOT_FOUND = { detail: "Not found" };
const LOCKED = { detail: "This record is locked; its custom fields cannot be changed" };
const REFERENCED = { detail: "Customer is referenced by other records" };
const VALIDATION_FAILED = {
  detail: {
    code: "validation_failed",
    event: "complete",
    message: "The complete step was blocked: 2 problem(s) must be fixed first",
    total: 2,
    problems: [
      { code: "custom_field.required", message: "Owner is required", entity_type: "transaction_line", entity_id: "L1", field: "owner", label: "Owner" },
      { code: "custom_field.required", message: "Project is required", entity_type: "transaction", entity_id: "T1", field: "project_ref", label: "Project" },
    ],
  },
};
const UNPROCESSABLE = {
  detail: [
    { loc: ["body", "name"], msg: "Field required", type: "missing" },
    { loc: ["body", "name"], msg: "String should have at least 1 character", type: "string_too_short" },
    { loc: ["body", "lines", 1, "item_id"], msg: "Item not found", type: "reference.not_found" },
    { loc: ["body", "values", "owner"], msg: "Owner is required", type: "custom_field.required" },
    { loc: ["query", "limit"], msg: "Input should be less than or equal to 200", type: "less_than_equal" },
    { loc: ["body"], msg: "Value error, something about the whole body", type: "value_error" },
  ],
};

describe("normalizeError", () => {
  it("401 is unauthorized", () => {
    expect(normalizeError(401, NOT_AUTHENTICATED)).toEqual({ kind: "unauthorized", status: 401, message: "Not authenticated" });
  });

  it("403 is forbidden and keeps the backend message", () => {
    expect(normalizeError(403, ROLE)).toMatchObject({ kind: "forbidden", status: 403, message: ROLE.detail });
  });

  it("404 is not found, with or without a body", () => {
    expect(normalizeError(404, NOT_FOUND)).toMatchObject({ kind: "not_found", message: "Not found" });
    expect(normalizeError(404, undefined)).toMatchObject({ kind: "not_found", message: "Not found." });
  });

  it("409 with a text detail is a plain conflict", () => {
    expect(normalizeError(409, LOCKED)).toEqual({ kind: "conflict", status: 409, message: LOCKED.detail, problems: [] });
    expect(normalizeError(409, REFERENCED)).toMatchObject({ kind: "conflict", message: REFERENCED.detail, problems: [] });
  });

  it("409 validation_failed carries structured problems to locate the record and field", () => {
    const error = normalizeError(409, VALIDATION_FAILED);

    expect(error.kind).toBe("conflict");
    if (error.kind !== "conflict") return;
    expect(error.code).toBe("validation_failed");
    expect(error.message).toContain("2 problem(s)");
    expect(error.problems).toEqual(VALIDATION_FAILED.detail.problems);
  });

  it("409 stale_record keeps its code and the current version", () => {
    const error = normalizeError(409, {
      detail: { code: "stale_record", message: "This record was changed by someone else", entity_type: "transaction_line", entity_id: "x", current_version: 4 },
    });
    expect(error).toEqual({
      kind: "conflict",
      status: 409,
      message: "This record was changed by someone else",
      code: "stale_record",
      problems: [],
      currentVersion: 4,
    });
  });

  it("428 and 400 about a missing or malformed version are client errors with the backend text", () => {
    expect(normalizeError(428, { detail: "This change must say which version it is based on" })).toMatchObject({ kind: "client", status: 428 });
    expect(normalizeError(400, { detail: "Invalid If-Match header" })).toMatchObject({ kind: "client", status: 400, message: "Invalid If-Match header" });
  });

  it("422 maps each location to a dotted field path, dropping the leading body", () => {
    const error = normalizeError(422, UNPROCESSABLE);

    expect(error.kind).toBe("validation");
    if (error.kind !== "validation") return;
    expect(error.fieldErrors).toEqual({
      name: ["Field required", "String should have at least 1 character"],
      "lines.1.item_id": ["Item not found"],
      "values.owner": ["Owner is required"],
      "query.limit": ["Input should be less than or equal to 200"],
    });
    expect(error.formErrors).toEqual(["something about the whole body"]); // the "Value error, " prefix is dropped
  });

  it("422 keeps the backend's wording for a decimal field but drops the Pydantic prefix", () => {
    const error = normalizeError(422, {
      detail: [{ loc: ["body", "price_ex_vat"], msg: "Value error, must be a non-negative decimal within the allowed precision; send it as a decimal string", type: "value_error" }],
    });
    expect(error).toMatchObject({
      kind: "validation",
      fieldErrors: { price_ex_vat: ["must be a non-negative decimal within the allowed precision; send it as a decimal string"] },
    });
  });

  it("422 with a text detail becomes a form-level error", () => {
    const error = normalizeError(422, { detail: "plain text" });
    expect(error).toMatchObject({ kind: "validation", fieldErrors: {}, formErrors: ["plain text"] });
  });

  it.each([500, 502, 503, 504])("%i is a server error that does not leak the body", (status) => {
    const error = normalizeError(status, { detail: "Traceback: secret internals" });
    expect(error.kind).toBe("server");
    expect(error.message).not.toContain("secret");
  });

  it("other 4xx are client errors", () => {
    expect(normalizeError(400, { detail: "Multiple organizations" })).toMatchObject({ kind: "client", status: 400, message: "Multiple organizations" });
    expect(normalizeError(413, undefined)).toMatchObject({ kind: "client", status: 413 });
  });

  it.each([undefined, null, "text", 42, [], { nothing: true }])("survives an unexpected body: %j", (body) => {
    for (const status of [401, 403, 404, 409, 422, 500, 418]) {
      expect(() => normalizeError(status, body)).not.toThrow();
    }
  });

  it("tolerates malformed problems and validation items", () => {
    const error = normalizeError(409, { detail: { code: "validation_failed", problems: [null, "x", { field: 5 }, { code: "a", message: "m", entity_type: "t", entity_id: "i", field: "f", label: null }] } });
    expect(error.kind === "conflict" && error.problems).toHaveLength(2);
    expect(normalizeError(422, { detail: [null, 5, { loc: "x" }, { msg: 3, loc: ["body", "a"] }] })).toMatchObject({
      kind: "validation",
      fieldErrors: { a: ["Not valid"] },
      formErrors: ["Not valid"],
    });
  });
});

describe("fieldPath", () => {
  it("joins the location and drops a leading body", () => {
    expect(fieldPath(["body", "values", "owner"])).toBe("values.owner");
    expect(fieldPath(["body", "lines", 0, "quantity"])).toBe("lines.0.quantity");
    expect(fieldPath(["query", "limit"])).toBe("query.limit");
  });

  it("is null when nothing is left or the input is not a location", () => {
    expect(fieldPath(["body"])).toBeNull();
    expect(fieldPath([])).toBeNull();
    expect(fieldPath("body")).toBeNull();
    expect(fieldPath(undefined)).toBeNull();
  });
});

describe("networkError", () => {
  it("is a distinct, retryable kind", () => {
    expect(networkError()).toMatchObject({ kind: "network", status: 0 });
  });
});
