import { describe, expect, it } from "vitest";

import { SUPPORT_EMAIL, supportMailto } from "@/lib/support";

const parts = (url: string) => {
  const [, query] = url.split("?");
  return new URLSearchParams(query);
};

describe("supportMailto", () => {
  it("writes to the support address with a subject and a template, spaces as %20", () => {
    const url = supportMailto(null);
    expect(url.startsWith(`mailto:${SUPPORT_EMAIL}?`)).toBe(true);
    expect(url).not.toContain("+");
    expect(parts(url).get("subject")).toBe("Support request");
    expect(parts(url).get("body")).toContain("I need help with:");
    expect(parts(url).get("body")).not.toContain("Account:");
  });

  it("names the account when signed in, and puts nothing else about the person in", () => {
    const body = parts(supportMailto({ name: "Fredrik Axö", email: "fredrik@example.test" })).get("body");
    expect(body).toContain("Account: fredrik@example.test (Fredrik Axö)");
    expect(body).not.toMatch(/password|token|session/i);
  });

  it("copes with a missing name and with characters that need escaping", () => {
    const url = supportMailto({ name: "", email: "a&b=c@example.test" });
    expect(parts(url).get("body")).toContain("Account: a&b=c@example.test\n".trimEnd());
    expect(url.split("?").length).toBe(2); // the address did not start a second query
  });
});
