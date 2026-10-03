import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  // The real functions throw to stop rendering; the tests need to see which one was called.
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  redirect: vi.fn((to: string) => {
    throw new Error(`NEXT_REDIRECT ${to}`);
  }),
}));
vi.mock("@/lib/identity", () => ({ getIdentity: vi.fn() }));
vi.mock("@/lib/backend", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/backend")>()), backendFetch: vi.fn() }));

import { backendFetch } from "@/lib/backend";
import { getIdentity } from "@/lib/identity";
import { requireUuid, serverRead } from "@/lib/server-api";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  vi.mocked(backendFetch).mockReset();
  vi.mocked(getIdentity).mockReset();
  vi.mocked(getIdentity).mockResolvedValue("fredrik@dev.test");
});

describe("serverRead", () => {
  it("asks the backend as the cookie identity, for the organization in the URL", async () => {
    vi.mocked(backendFetch).mockResolvedValue(json(200, [{ id: "1" }]));

    await expect(serverRead(ORG, "/api/customers", "?limit=26")).resolves.toEqual([{ id: "1" }]);

    expect(backendFetch).toHaveBeenCalledWith({ email: "fredrik@dev.test", orgId: ORG }, "/api/customers", { search: "?limit=26" });
  });

  it("does not even call the backend for a malformed organization id", async () => {
    await expect(serverRead("not-a-uuid", "/api/customers")).rejects.toThrow("NEXT_NOT_FOUND");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("sends a visitor without identity to sign in, without calling the backend", async () => {
    vi.mocked(getIdentity).mockResolvedValue(null);
    await expect(serverRead(ORG, "/api/customers")).rejects.toThrow("NEXT_REDIRECT /dev-login");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("sends a 401 to sign in", async () => {
    vi.mocked(backendFetch).mockResolvedValue(json(401, { detail: "Not authenticated" }));
    await expect(serverRead(ORG, "/api/customers")).rejects.toThrow("NEXT_REDIRECT /dev-login");
  });

  it("treats every 404 as the same generic not-found, whatever FastAPI said", async () => {
    vi.mocked(backendFetch).mockResolvedValueOnce(json(404, { detail: "Not found" }));
    await expect(serverRead(ORG, "/api/customers/x")).rejects.toThrow("NEXT_NOT_FOUND");
    vi.mocked(backendFetch).mockResolvedValueOnce(json(404, { detail: "Customer belongs to another organization" }));
    await expect(serverRead(ORG, "/api/customers/y")).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it.each([403, 409, 422, 500, 502])("turns a %i into an error for the error page, with no backend details", async (status) => {
    vi.mocked(backendFetch).mockResolvedValue(json(status, { detail: "Traceback: secret internals" }));
    const failure = serverRead(ORG, "/api/customers");
    await expect(failure).rejects.toThrow(`The backend answered ${status}`);
    await expect(failure).rejects.not.toThrow(/secret/);
  });

  it("turns an unreachable backend into an error without leaking the cause", async () => {
    vi.mocked(backendFetch).mockRejectedValue(new Error("connect ECONNREFUSED 10.1.2.3:8000"));
    const failure = serverRead(ORG, "/api/customers");
    await expect(failure).rejects.toThrow("The backend could not be reached");
    await expect(failure).rejects.not.toThrow(/10\.1\.2\.3/);
  });
});

describe("requireUuid", () => {
  it("returns a UUID and treats anything else as not found", () => {
    expect(requireUuid(ORG)).toBe(ORG);
    for (const bad of ["abc", "../customers", "1", ORG + "x", ""]) expect(() => requireUuid(bad)).toThrow("NEXT_NOT_FOUND");
  });
});
