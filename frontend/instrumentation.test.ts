// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { register } from "./instrumentation";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

function env(values: Record<string, string | undefined>) {
  for (const name of ["AUTH_MODE", "APP_ENV", "PUBLIC_ORIGIN", "NEXT_RUNTIME"]) vi.stubEnv(name, undefined as unknown as string);
  for (const [name, value] of Object.entries(values)) if (value !== undefined) vi.stubEnv(name, value);
}

describe("the server refuses to start with an unusable authentication configuration (production)", () => {
  it.each([
    ["nothing configured", {}],
    ["AUTH_MODE=dev", { AUTH_MODE: "dev" }],
    ["AUTH_MODE=dev even with APP_ENV unset", { AUTH_MODE: "dev", APP_ENV: "" }],
    ["session without PUBLIC_ORIGIN", { AUTH_MODE: "session" }],
    ["session with an http origin", { AUTH_MODE: "session", PUBLIC_ORIGIN: "http://app.example.com" }],
    ["an unknown mode", { AUTH_MODE: "jwt", PUBLIC_ORIGIN: "https://app.example.com" }],
  ])("%s", async (_name, values) => {
    env({ NEXT_RUNTIME: "nodejs", ...values });
    const exit = vi.spyOn(process, "exit").mockImplementation((() => undefined) as never);
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    await register();
    expect(exit).toHaveBeenCalledWith(1); // the process dies: it does not linger answering 500s
    expect(error).toHaveBeenCalledWith(expect.stringMatching(/Authentication is misconfigured/));
  });

  it("starts with a valid production session configuration", async () => {
    env({ NEXT_RUNTIME: "nodejs", AUTH_MODE: "session", PUBLIC_ORIGIN: "https://app.example.com" });
    const exit = vi.spyOn(process, "exit").mockImplementation((() => undefined) as never);
    await expect(register()).resolves.toBeUndefined();
    expect(exit).not.toHaveBeenCalled();
  });
});

describe("development", () => {
  it("only warns about a bad configuration (every request is refused anyway)", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    env({ NEXT_RUNTIME: "nodejs", APP_ENV: "development" });
    const exit = vi.spyOn(process, "exit").mockImplementation((() => undefined) as never);
    await expect(register()).resolves.toBeUndefined();
    expect(exit).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("Authentication is misconfigured"));
  });

  it("starts quietly with the dev identity or a local session configuration", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    env({ NEXT_RUNTIME: "nodejs", APP_ENV: "development", AUTH_MODE: "dev" });
    await register();
    env({ NEXT_RUNTIME: "nodejs", APP_ENV: "development", AUTH_MODE: "session", PUBLIC_ORIGIN: "http://localhost:3000" });
    await register();
    expect(warn).not.toHaveBeenCalled();
  });
});

describe("other runtimes", () => {
  it("does nothing outside the Node.js runtime", async () => {
    env({ NEXT_RUNTIME: "edge" });
    await expect(register()).resolves.toBeUndefined();
  });
});
