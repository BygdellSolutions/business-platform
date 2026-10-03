import { describe, expect, it } from "vitest";

import { EMPTY_PROFILE, PROFILE_FIELDS, profileBody, profileChanges, profileState } from "@/lib/profile";

describe("profile helpers", () => {
  it("covers exactly the seven optional fields", () => {
    expect(PROFILE_FIELDS).toEqual(["address_line1", "address_line2", "postal_code", "city", "country_code", "registration_number", "vat_number"]);
    expect(Object.keys(EMPTY_PROFILE)).toEqual(PROFILE_FIELDS);
  });

  it("shows null as an empty box and keeps every value as it is", () => {
    expect(profileState({ city: "Umeå", vat_number: null })).toEqual({ ...profileState(), city: "Umeå" });
    expect(profileState()).toEqual(Object.fromEntries(PROFILE_FIELDS.map((f) => [f, ""])));
  });

  it("sends a blank box as null and anything else exactly as typed (no trimming or case rules of its own)", () => {
    const state = { ...profileState(), address_line1: "  Ridvägen 2  ", city: "   ", country_code: "se" };
    expect(profileBody(state)).toEqual({ ...EMPTY_PROFILE, address_line1: "  Ridvägen 2  ", country_code: "se" });
  });

  it("reports only the fields that differ from the saved record", () => {
    const saved = { ...EMPTY_PROFILE, city: "Umeå", vat_number: "SE1" };
    expect(profileChanges(profileState(saved), saved)).toEqual({});
    expect(profileChanges({ ...profileState(saved), city: "Luleå" }, saved)).toEqual({ city: "Luleå" });
    expect(profileChanges({ ...profileState(saved), vat_number: "" }, saved)).toEqual({ vat_number: null });
    expect(profileChanges({ ...profileState(saved), postal_code: "903 26" }, saved)).toEqual({ postal_code: "903 26" });
  });

  it("treats a missing saved value like null, so an untouched blank box is not a change", () => {
    expect(profileChanges(profileState(), {})).toEqual({});
  });
});
