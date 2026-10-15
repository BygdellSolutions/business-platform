import { describe, expect, it } from "vitest";

import { isCodeAction, isTypedCode, parseEnrollment, parseMfaChallenge, parseMfaStatus, parseRecoveryCodes } from "@/lib/auth/own-mfa";

const CHALLENGE = "C".repeat(43);
const SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
const URI = `otpauth://totp/Business%20Platform%3Aada%40example.test?secret=${SECRET}&issuer=Business%20Platform`;

describe("parseMfaChallenge", () => {
  it("accepts the answer that says a code is needed", () => {
    expect(parseMfaChallenge({ mfa_required: true, challenge: CHALLENGE, expires_at: "2026-10-15T12:05:00Z" })).toBe(CHALLENGE);
  });

  it.each([
    ["a session answer", { token: "T".repeat(43), csrf_token: "C".repeat(43), expires_at: "x", user: {} }],
    ["mfa_required false", { mfa_required: false, challenge: CHALLENGE }],
    ["a challenge of the wrong shape", { mfa_required: true, challenge: "short" }],
    ["a challenge with other characters", { mfa_required: true, challenge: `${"C".repeat(42)}!` }],
    ["nothing", null],
  ])("refuses %s", (_name, value) => {
    expect(parseMfaChallenge(value)).toBeNull();
  });
});

describe("parseMfaStatus", () => {
  it("reduces the answer to its three fields", () => {
    expect(parseMfaStatus({ enabled: true, required: false, recovery_codes_left: 7, seed: "never" })).toEqual({ enabled: true, required: false, recoveryCodesLeft: 7 });
  });

  it.each([{ enabled: "yes", required: false, recovery_codes_left: 1 }, { enabled: true, required: false, recovery_codes_left: -1 }, { enabled: true, required: false, recovery_codes_left: 1.5 }, { enabled: true }, null])(
    "refuses %j",
    (value) => {
      expect(parseMfaStatus(value)).toBeNull();
    },
  );
});

describe("parseEnrollment", () => {
  it("accepts a base32 key and an otpauth link", () => {
    expect(parseEnrollment({ secret: SECRET, uri: URI, extra: "dropped" })).toEqual({ secret: SECRET, uri: URI });
  });

  it.each([
    [{ secret: "not-base32", uri: URI }],
    [{ secret: SECRET, uri: "https://evil.example/otpauth" }],
    [{ secret: SECRET, uri: `otpauth://totp/${"x".repeat(2000)}` }],
    [{ secret: SECRET }],
  ])("refuses %j", (value) => {
    expect(parseEnrollment(value)).toBeNull();
  });
});

describe("parseRecoveryCodes", () => {
  const codes = Array.from({ length: 10 }, (_, i) => `${String(i).repeat(8)}-${"abcdef01".slice(0, 8)}`);

  it("accepts the list of codes and nothing else from the answer", () => {
    expect(parseRecoveryCodes({ recovery_codes: codes, other: 1 })).toEqual(codes);
  });

  it.each([
    [{ recovery_codes: [] }],
    [{ recovery_codes: ["not-a-code"] }],
    [{ recovery_codes: [...codes, ...codes, "00000000-00000000"] }],
    [{ recovery_codes: "00000000-00000000" }],
    [{}],
  ])("refuses %j", (value) => {
    expect(parseRecoveryCodes(value)).toBeNull();
  });
});

describe("small guards", () => {
  it("knows the three actions that take a code", () => {
    expect(["confirm", "recovery-codes", "disable"].every(isCodeAction)).toBe(true);
    expect(["enroll", "__proto__", "", "CONFIRM"].some(isCodeAction)).toBe(false);
  });

  it("accepts a typed code of sensible size only", () => {
    expect(isTypedCode("123456")).toBe(true);
    expect(isTypedCode("  ")).toBe(false);
    expect(isTypedCode("1".repeat(65))).toBe(false);
    expect(isTypedCode(123456)).toBe(false);
  });
});
