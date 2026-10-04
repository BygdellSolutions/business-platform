import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  // The real functions throw to stop rendering; the tests need to see which one was called.
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  redirect: vi.fn((to: string) => {
    throw new Error(`NEXT_REDIRECT ${to}`);
  }),
}));
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/lib/backend", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/backend")>()), backendFetch: vi.fn() }));

import { cookies } from "next/headers";

import { backendFetch } from "@/lib/backend";
import { requireUuid, serverRead, serverReadOrNull } from "@/lib/server-api";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const TOKEN = "T".repeat(43);
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function cookieJar(values: Record<string, string>) {
  vi.mocked(cookies).mockResolvedValue({ get: (name: string) => (name in values ? { name, value: values[name] } : undefined) } as never);
}

/** The same behaviour must hold whichever identity mechanism is active. */
const MODES = [
  { name: "dev", env: { AUTH_MODE: "dev", APP_ENV: "development" }, signedIn: { bp_dev_user: "fredrik@dev.test" }, credential: { kind: "dev", email: "fredrik@dev.test" }, login: "/dev-login" },
  {
    name: "session",
    env: { AUTH_MODE: "session", APP_ENV: "development", PUBLIC_ORIGIN: "http://localhost:3000" },
    signedIn: { bp_session: TOKEN },
    credential: { kind: "session", token: TOKEN },
    login: `/login?next=${encodeURIComponent(`/o/${ORG}`)}`,
  },
] as const;

beforeEach(() => {
  vi.mocked(backendFetch).mockReset();
  vi.mocked(cookies).mockReset();
});
afterEach(() => vi.unstubAllEnvs());

describe.each(MODES)("serverRead in $name mode", (mode) => {
  beforeEach(() => {
    for (const [key, value] of Object.entries(mode.env)) vi.stubEnv(key, value);
    cookieJar(mode.signedIn);
  });

  it("asks the backend as the cookie credential, for the organization in the URL", async () => {
    vi.mocked(backendFetch).mockResolvedValue(json(200, [{ id: "1" }]));

    await expect(serverRead(ORG, "/api/customers", "?limit=26")).resolves.toEqual([{ id: "1" }]);

    expect(backendFetch).toHaveBeenCalledWith({ credential: mode.credential, orgId: ORG }, "/api/customers", { search: "?limit=26" });
  });

  it("does not even call the backend for a malformed organization id", async () => {
    await expect(serverRead("not-a-uuid", "/api/customers")).rejects.toThrow("NEXT_NOT_FOUND");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("sends a visitor without a credential to sign in, with a safe way back, without calling the backend", async () => {
    cookieJar({});
    await expect(serverRead(ORG, "/api/customers")).rejects.toThrow(`NEXT_REDIRECT ${mode.login}`);
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("sends a 401 (expired, revoked, disabled) to sign in", async () => {
    vi.mocked(backendFetch).mockResolvedValue(json(401, { detail: "Not authenticated" }));
    await expect(serverRead(ORG, "/api/customers")).rejects.toThrow(`NEXT_REDIRECT ${mode.login}`);
  });

  it("treats every 404 as the same generic not-found, whatever FastAPI said", async () => {
    vi.mocked(backendFetch).mockResolvedValueOnce(json(404, { detail: "Not found" }));
    await expect(serverRead(ORG, "/api/customers/x")).rejects.toThrow("NEXT_NOT_FOUND");
    vi.mocked(backendFetch).mockResolvedValueOnce(json(404, { detail: "Customer belongs to another organization" }));
    await expect(serverRead(ORG, "/api/customers/y")).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it.each([403, 409, 422, 500, 502])("turns a %i into an error for the error page, with no backend details (a 403 is NOT a login redirect)", async (status) => {
    vi.mocked(backendFetch).mockResolvedValue(json(status, { detail: "Traceback: secret internals" }));
    const failure = serverRead(ORG, "/api/customers");
    await expect(failure).rejects.toThrow(`The backend answered ${status}`);
    await expect(failure).rejects.not.toThrow(/secret|NEXT_REDIRECT/);
  });

  it("turns an unreachable backend into an error without leaking the cause", async () => {
    vi.mocked(backendFetch).mockRejectedValue(new Error("connect ECONNREFUSED 10.1.2.3:8000"));
    const failure = serverRead(ORG, "/api/customers");
    await expect(failure).rejects.toThrow("The backend could not be reached");
    await expect(failure).rejects.not.toThrow(/10\.1\.2\.3/);
  });

  describe("serverReadOrNull (a record that only decorates a page)", () => {
    it("returns the record, asked for the organization in the URL", async () => {
      vi.mocked(backendFetch).mockResolvedValue(json(200, { id: "1", name: "Anna" }));
      await expect(serverReadOrNull(ORG, "/api/customers/1")).resolves.toEqual({ id: "1", name: "Anna" });
      expect(backendFetch).toHaveBeenCalledWith({ credential: mode.credential, orgId: ORG }, "/api/customers/1", { search: "" });
    });

    it("gives null for a 404, whatever FastAPI said: a foreign id and a random id cannot be told apart", async () => {
      vi.mocked(backendFetch).mockResolvedValueOnce(json(404, { detail: "Not found" }));
      const random = await serverReadOrNull(ORG, "/api/customers/x");
      vi.mocked(backendFetch).mockResolvedValueOnce(json(404, { detail: "Customer belongs to another organization" }));
      const foreign = await serverReadOrNull(ORG, "/api/customers/y");

      expect(random).toBeNull();
      expect(foreign).toBe(random);
    });

    it("still sends a 401 to sign in, still turns other failures into errors, and still refuses a malformed organization", async () => {
      vi.mocked(backendFetch).mockResolvedValueOnce(json(401, { detail: "Not authenticated" }));
      await expect(serverReadOrNull(ORG, "/api/customers/x")).rejects.toThrow(`NEXT_REDIRECT ${mode.login}`);
      vi.mocked(backendFetch).mockResolvedValueOnce(json(500, { detail: "Traceback" }));
      await expect(serverReadOrNull(ORG, "/api/customers/x")).rejects.toThrow("The backend answered 500");
      await expect(serverReadOrNull("not-a-uuid", "/api/customers/x")).rejects.toThrow("NEXT_NOT_FOUND");
    });
  });
});

describe("the modes never borrow each other's credential", () => {
  it("session mode ignores the dev cookie", async () => {
    for (const [key, value] of Object.entries(MODES[1].env)) vi.stubEnv(key, value);
    cookieJar({ bp_dev_user: "fredrik@dev.test" });
    await expect(serverRead(ORG, "/api/customers")).rejects.toThrow("NEXT_REDIRECT /login?next=");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("dev mode ignores a session cookie", async () => {
    for (const [key, value] of Object.entries(MODES[0].env)) vi.stubEnv(key, value);
    cookieJar({ bp_session: TOKEN });
    await expect(serverRead(ORG, "/api/customers")).rejects.toThrow("NEXT_REDIRECT /dev-login");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("no mode at all means no credential", async () => {
    vi.stubEnv("AUTH_MODE", "");
    cookieJar({ bp_dev_user: "fredrik@dev.test", bp_session: TOKEN });
    await expect(serverRead(ORG, "/api/customers")).rejects.toThrow(/NEXT_REDIRECT/);
    expect(backendFetch).not.toHaveBeenCalled();
  });
});

describe("requireUuid", () => {
  it("returns a UUID and treats anything else as not found", () => {
    expect(requireUuid(ORG)).toBe(ORG);
    for (const bad of ["abc", "../customers", "1", ORG + "x", ""]) expect(() => requireUuid(bad)).toThrow("NEXT_NOT_FOUND");
  });
});
