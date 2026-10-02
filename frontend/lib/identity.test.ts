// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { devIdentityEnabled, identityFromCookie, parseEmail } from "@/lib/identity";

afterEach(() => vi.unstubAllEnvs());

describe("dev identity fails closed", () => {
  it.each([undefined, "", "disabled", "true", "1", "ENABLED"])("is disabled when DEV_IDENTITY=%j", (value) => {
    if (value === undefined) vi.stubEnv("DEV_IDENTITY", undefined as unknown as string);
    else vi.stubEnv("DEV_IDENTITY", value);
    expect(devIdentityEnabled()).toBe(false);
    expect(identityFromCookie("fredrik@dev.test")).toBeNull();
  });

  it("is enabled only by DEV_IDENTITY=enabled", () => {
    vi.stubEnv("DEV_IDENTITY", "enabled");
    expect(devIdentityEnabled()).toBe(true);
    expect(identityFromCookie("fredrik@dev.test")).toBe("fredrik@dev.test");
  });

  it("has no ambient identity: nothing in the environment stands in for a missing cookie", () => {
    vi.stubEnv("DEV_IDENTITY", "enabled");
    vi.stubEnv("DEV_USER_EMAIL", "fredrik@dev.test");
    expect(identityFromCookie(undefined)).toBeNull();
    expect(identityFromCookie("")).toBeNull();
  });
});

describe("parseEmail", () => {
  it("normalizes case and surrounding whitespace, including a trailing line break", () => {
    expect(parseEmail("  Fredrik@Dev.Test ")).toBe("fredrik@dev.test");
    expect(parseEmail("a@c.test\r\n")).toBe("a@c.test"); // trimmed clean; an EMBEDDED newline is rejected below
  });

  it.each(["", "   ", "plain", "a@b", "a b@c.test", "@c.test", "a@@c.test", "a@c.test\nX-Org: 1", `${"x".repeat(70)}@c.test`, `a@${"x".repeat(300)}.test`, undefined, null])("rejects %j", (value) => {
    expect(parseEmail(value as string)).toBeNull();
  });
});
