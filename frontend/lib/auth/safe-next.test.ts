import { describe, expect, it } from "vitest";

import { DEFAULT_NEXT, safeNext } from "@/lib/auth/safe-next";

const ORG = "00000000-0000-4000-8000-0000000000a1";

describe("safeNext allows only the application's own relative destinations", () => {
  it.each([
    ["/", "/"],
    [`/o/${ORG}`, `/o/${ORG}`],
    [`/o/${ORG}/`, `/o/${ORG}/`],
    [`/o/${ORG}/customers`, `/o/${ORG}/customers`],
    [`/o/${ORG}/customers/${ORG}`, `/o/${ORG}/customers/${ORG}`],
    [`/o/${ORG}/invoices/new`, `/o/${ORG}/invoices/new`],
    [`/o/${ORG}/customers?page=2&q=anna`, `/o/${ORG}/customers?page=2&q=anna`],
    [`/o/${ORG.toUpperCase()}/customers`, `/o/${ORG.toUpperCase()}/customers`],
    ["/?x=1", "/?x=1"],
  ])("keeps %j", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });

  it.each([
    // absolute URLs, scheme-relative and scheme tricks
    "https://evil.example/",
    "http://evil.example",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "data:text/html,x",
    "mailto:a@b.c",
    "evil.example/path",
    "",
    "o/relative/without/slash",
    // backslashes, whitespace, control characters (raw and encoded)
    `/o/${ORG}\\customers`,
    "/ //evil.example",
    "/\t/evil.example",
    "/\nHost: evil.example",
    "/\u0000",
    `/o/${ORG}/customers%5cevil`,
    `/o/${ORG}/%2fevil.example`,
    "/%2F%2Fevil.example",
    "/%5Cevil.example",
    "/%2e%2e/%2e%2e/",
    "/o/%2e%2e/x",
    "/%00",
    "/%0d%0aSet-Cookie:x=1",
    // dot segments and empty segments
    `/o/${ORG}/../../etc`,
    `/o/${ORG}/./customers`,
    `/o/${ORG}//customers`,
    "/o/..",
    // outside the allowlist
    "/login",
    "/login?next=/",
    "/setup",
    "/dev-login",
    "/api/auth/logout",
    `/api/o/${ORG}/customers`,
    "/o",
    "/o/",
    "/o/not-a-uuid",
    "/o/not-a-uuid/customers",
    `/p/${ORG}`,
    `/o/${ORG}/customers#fragment`,
    `/o/${ORG}/cust omers`,
    `/o/${ORG}/customers?a=<script>`,
    `/o/${ORG}/customers?a=b"c`,
    "/?next=//evil.example/\\",
  ])("falls back to / for %j", (input) => {
    expect(safeNext(input)).toBe(DEFAULT_NEXT);
  });

  it("falls back for anything that is not a short string", () => {
    for (const value of [undefined, null, 42, {}, [], ["/o"], "x".repeat(600), `/o/${ORG}/${"a".repeat(600)}`]) expect(safeNext(value)).toBe("/");
  });

  it("never returns anything that is not an application-relative path", () => {
    const hostile = ["//a.example", "/\\a.example", "https://a.example", "/%2F/a.example", `/o/${ORG}/..%2f..%2f`, "/o/‮/x", "/о/" + ORG];
    for (const value of hostile) {
      const out = safeNext(value);
      expect(out.startsWith("/") && !out.startsWith("//") && !out.includes("\\")).toBe(true);
      expect(new URL(out, "http://app.test").origin).toBe("http://app.test");
    }
  });
});
