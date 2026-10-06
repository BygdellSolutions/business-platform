// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The end-to-end support code must never silently ask CI for a browser the runner does not have (Microsoft Edge), and CI keeps no
 * traces (they hold cookies, tokens and one-time links). The behaviour is in e2e/env.ts, outside the app; it is checked here
 * so that the ordinary frontend gate fails if it regresses.
 */

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function env(values: Record<string, string | undefined>) {
  vi.resetModules();
  for (const name of ["CI", "E2E_BROWSER"]) vi.stubEnv(name, undefined as unknown as string);
  for (const [name, value] of Object.entries(values)) if (value !== undefined) vi.stubEnv(name, value);
  return import("../e2e/env");
}

describe("browserChannel", () => {
  it("uses E2E_BROWSER when it is set; `chromium` is Playwright's own Chromium in its default headless mode (no channel)", async () => {
    expect((await env({ CI: "true", E2E_BROWSER: "chromium" })).browserChannel()).toBeUndefined(); // NOT the "new headless" channel
    expect((await env({ E2E_BROWSER: "chrome" })).browserChannel()).toBe("chrome");
    expect((await env({ E2E_BROWSER: "msedge" })).browserChannel()).toBe("msedge");
  });

  it("falls back to the installed Microsoft Edge on a developer machine only", async () => {
    expect((await env({})).browserChannel()).toBe("msedge");
  });

  it("has NO fallback in CI: an unset E2E_BROWSER stops the run", async () => {
    const { browserChannel } = await env({ CI: "true" });
    expect(() => browserChannel()).toThrow(/E2E_BROWSER is not set/);
  });
});

describe("traces", () => {
  it("are off in CI and retained on failure locally", async () => {
    expect((await env({ CI: "true", E2E_BROWSER: "chromium" })).TRACE_MODE).toBe("off");
    expect((await env({})).TRACE_MODE).toBe("retain-on-failure");
  });
});
