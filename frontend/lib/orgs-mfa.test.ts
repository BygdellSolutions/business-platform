import { describe, expect, it } from "vitest";

import { needsMfaSetup, type CurrentUser } from "@/lib/orgs";

const user = (over: Partial<CurrentUser> = {}): CurrentUser => ({
  id: "u",
  email: "ada@example.test",
  name: "Ada",
  can_create_organizations: false,
  created_organizations: 0,
  max_owned_organizations: 0,
  ...over,
});

describe("needsMfaSetup", () => {
  it("is true only for a person whose role requires a second factor and who has none", () => {
    expect(needsMfaSetup(user({ mfa_required: true, mfa_enabled: false }))).toBe(true);
    expect(needsMfaSetup(user({ mfa_required: true }))).toBe(true); // the enabled flag missing counts as not enabled
  });

  it.each([
    [{ mfa_required: true, mfa_enabled: true }],
    [{ mfa_required: false, mfa_enabled: false }],
    [{}], // an older backend that does not say: never trap anyone behind a screen the server did not ask for
  ])("is false for %j", (fields) => {
    expect(needsMfaSetup(user(fields))).toBe(false);
  });
});
