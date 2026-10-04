import { describe, expect, it } from "vitest";

import { inviteLink, isInviteToken, normalizeEmail, tokenFromFragment } from "@/lib/invite";

const TOKEN = "abcDEF0123456789abcDEF0123456789_-abcdefghi";

describe("invite helpers", () => {
  it("accepts only a 43-character url-safe token", () => {
    expect(TOKEN).toHaveLength(43);
    expect(isInviteToken(TOKEN)).toBe(true);
    for (const bad of ["", "short", TOKEN + "a", TOKEN.slice(1) + "+", TOKEN.slice(1) + "/", TOKEN.slice(1) + " ", null, undefined, 7]) expect(isInviteToken(bad)).toBe(false);
  });

  it("builds the link with the secret in the FRAGMENT, never in the path or the query", () => {
    const link = inviteLink("https://app.example.com", TOKEN);
    expect(link).toBe(`https://app.example.com/invite#${TOKEN}`);
    const url = new URL(link);
    expect(url.pathname).toBe("/invite");
    expect(url.search).toBe("");
    expect(url.hash).toBe(`#${TOKEN}`);
  });

  it("reads a fragment: undefined when empty, null when malformed, the token when well formed", () => {
    expect(tokenFromFragment("")).toBeUndefined();
    expect(tokenFromFragment("#")).toBeUndefined();
    expect(tokenFromFragment("#nonsense")).toBeNull();
    expect(tokenFromFragment(`#${TOKEN}`)).toBe(TOKEN);
    expect(tokenFromFragment(TOKEN)).toBe(TOKEN);
  });

  it("normalizes an email like the backend (trim, lowercase)", () => {
    expect(normalizeEmail("  Mixed.Case@Example.COM ")).toBe("mixed.case@example.com");
  });
});
