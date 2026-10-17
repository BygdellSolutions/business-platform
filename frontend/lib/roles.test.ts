import { describe, expect, it } from "vitest";

import { canMutateInvoices, canWriteRecords, INVOICING_ROLES, RECORD_WRITER_ROLES } from "@/lib/roles";
import type { Role } from "@/lib/api/types";

describe("who is offered invoice controls", () => {
  it("is exactly owner, admin and accountant", () => {
    expect([...INVOICING_ROLES].sort()).toEqual(["accountant", "admin", "owner"]);
  });

  it.each<[Role, boolean]>([
    ["owner", true],
    ["admin", true],
    ["accountant", true],
    ["employee", false],
    ["viewer", false],
  ])("%s: %s", (role, allowed) => {
    expect(canMutateInvoices(role)).toBe(allowed);
  });

  it("an unknown role (the membership could not be read) gets nothing", () => {
    expect(canMutateInvoices(undefined)).toBe(false);
  });
});

describe("who is offered record controls", () => {
  it("is every role but viewer", () => {
    expect([...RECORD_WRITER_ROLES].sort()).toEqual(["accountant", "admin", "employee", "owner"]);
  });

  it.each<[Role, boolean]>([
    ["owner", true],
    ["admin", true],
    ["accountant", true],
    ["employee", true],
    ["viewer", false],
  ])("%s: %s", (role, allowed) => {
    expect(canWriteRecords(role)).toBe(allowed);
  });

  it("an unknown role (the membership could not be read) gets nothing", () => {
    expect(canWriteRecords(undefined)).toBe(false);
  });
});
