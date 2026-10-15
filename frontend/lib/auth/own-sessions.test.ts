import { describe, expect, it } from "vitest";

import { describeBrowser, parseSessionList } from "@/lib/auth/own-sessions";

const ID = "11111111-1111-4111-8111-111111111111";
const row = (over: Record<string, unknown> = {}) => ({
  id: ID,
  created_at: "2026-10-15T08:00:00Z",
  last_used_at: "2026-10-15T09:30:00Z",
  user_agent: "Mozilla/5.0 Chrome/120",
  current: false,
  ...over,
});

describe("parseSessionList", () => {
  it("keeps exactly the five fields a person needs", () => {
    expect(parseSessionList([row({ token: "SECRET", token_hash: "x", source: "10.0.0.1" })])).toEqual([
      { id: ID, createdAt: "2026-10-15T08:00:00Z", lastUsedAt: "2026-10-15T09:30:00Z", userAgent: "Mozilla/5.0 Chrome/120", current: false },
    ]);
  });

  it("accepts a missing user agent", () => {
    expect(parseSessionList([row({ user_agent: null })])?.[0].userAgent).toBeNull();
  });

  it.each([
    ["not an array", { sessions: [] }],
    ["a row that is not an object", [null]],
    ["an id that is not a UUID", [row({ id: "not-a-uuid" })]],
    ["a timestamp that is not a date", [row({ created_at: "yesterday" })]],
    ["current that is not a boolean", [row({ current: "yes" })]],
    ["a user agent that is not text", [row({ user_agent: 5 })]],
    ["a user agent that is too long", [row({ user_agent: "x".repeat(201) })]],
    ["more rows than any person has", Array.from({ length: 101 }, () => row())],
  ])("refuses %s", (_name, value) => {
    expect(parseSessionList(value)).toBeNull();
  });
});

describe("describeBrowser", () => {
  it.each([
    ["Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/120.0 Safari/537.36", "Chrome on Windows"],
    ["Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36 Edg/120.0", "Edge on Windows"],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Version/17 Safari/605.1", "Safari on macOS"],
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17 Safari/604.1", "Safari on iOS"],
    ["Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko Firefox/121.0", "Firefox on Linux"],
    [null, "Unknown browser"],
    ["curl/8.5.0", "curl/8.5.0"],
  ])("%s -> %s", (agent, expected) => {
    expect(describeBrowser(agent)).toBe(expected);
  });
});
