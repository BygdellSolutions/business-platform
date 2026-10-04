import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiDownloadPdf, apiFetch, loginTarget, setUnauthorizedHandler } from "@/lib/api/client";
import { CSRF_HEADER, readCsrfToken } from "@/lib/auth/cookies";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const CSRF = "C".repeat(43);

let fetchMock: ReturnType<typeof vi.fn>;
let unauthorized: ReturnType<typeof vi.fn<(loginPath: string) => void>>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  unauthorized = vi.fn<(loginPath: string) => void>();
  setUnauthorizedHandler(unauthorized);
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "bp_csrf=; Max-Age=0; path=/";
  document.cookie = "__Host-bp_csrf=; Max-Age=0; path=/";
});

const reply = (status: number, body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
const sentHeaders = (index = 0) => (fetchMock.mock.calls[index][1] as { headers: Record<string, string> }).headers;

describe("the CSRF header on state-changing requests", () => {
  it("echoes the readable CSRF cookie on POST, PATCH and DELETE", async () => {
    document.cookie = `bp_csrf=${CSRF}; path=/`;
    fetchMock.mockImplementation(() => reply(200, {}));

    for (const method of ["POST", "PATCH", "DELETE"] as const) await apiFetch(ORG, "/customers", { method, body: method === "DELETE" ? undefined : {} });

    for (let index = 0; index < 3; index++) expect(sentHeaders(index)[CSRF_HEADER]).toBe(CSRF);
  });

  it("does not send it on a read, and sends nothing when there is no CSRF cookie (the dev run)", async () => {
    fetchMock.mockImplementation(() => reply(200, []));
    await apiFetch(ORG, "/customers");
    expect(Object.keys(sentHeaders())).toEqual(["accept"]);

    await apiFetch(ORG, "/customers", { method: "POST", body: {} });
    expect(sentHeaders(1)[CSRF_HEADER]).toBeUndefined();
  });

  it("never sends an Authorization, Cookie or identity header of its own", async () => {
    document.cookie = `bp_csrf=${CSRF}; path=/`;
    fetchMock.mockImplementation(() => reply(200, {}));
    await apiFetch(ORG, "/customers", { method: "POST", body: {} });
    const names = Object.keys(sentHeaders()).map((name) => name.toLowerCase());
    expect(names.sort()).toEqual(["accept", "content-type", CSRF_HEADER].sort());
  });

  it("reads only the CSRF cookie, under either of its two names", () => {
    expect(readCsrfToken(`a=1; bp_csrf=${CSRF}; b=2`)).toBe(CSRF);
    expect(readCsrfToken(`__Host-bp_csrf=${CSRF}`)).toBe(CSRF);
    expect(readCsrfToken("bp_session=secret; other=x")).toBeNull();
    expect(readCsrfToken("")).toBeNull();
    expect(readCsrfToken("xbp_csrf=zzz")).toBeNull();
  });
});

describe("a lost session goes to the login the BFF named", () => {
  it("passes the BFF's login path to the handler (session mode)", async () => {
    fetchMock.mockImplementation(() => reply(401, { detail: "Not authenticated", login: "/login" }));
    const result = await apiFetch(ORG, "/customers");
    expect(result).toMatchObject({ ok: false, error: { kind: "unauthorized" } });
    expect(unauthorized).toHaveBeenCalledExactlyOnceWith("/login");
  });

  it("falls back to the dev login when the BFF named none, and for a body that is not JSON", async () => {
    fetchMock.mockReturnValueOnce(reply(401, { detail: "Not authenticated" }));
    await apiFetch(ORG, "/customers");
    fetchMock.mockReturnValueOnce(Promise.resolve(new Response("Unauthorized", { status: 401, headers: { "content-type": "text/plain" } })));
    await apiFetch(ORG, "/customers");
    expect(unauthorized.mock.calls).toEqual([["/dev-login"], ["/dev-login"]]);
  });

  it("does the same for a PDF download", async () => {
    fetchMock.mockImplementation(() => reply(401, { detail: "Not authenticated", login: "/login" }));
    await apiDownloadPdf(ORG, "/invoices/x/pdf");
    expect(unauthorized).toHaveBeenCalledExactlyOnceWith("/login");
  });

  it("a 403 is not an authentication failure", async () => {
    fetchMock.mockImplementation(() => reply(403, { detail: "Your role in this organization does not allow this action" }));
    const result = await apiFetch(ORG, "/organization", { method: "PATCH", body: {} });
    expect(result).toMatchObject({ ok: false, error: { kind: "forbidden" } });
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("a 404 is not an authentication failure", async () => {
    fetchMock.mockImplementation(() => reply(404, { detail: "Not found" }));
    await apiFetch(ORG, "/customers/x");
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("a 403 CSRF refusal from FastAPI is a plain refusal, not a login redirect", async () => {
    fetchMock.mockImplementation(() => reply(403, { detail: { code: "csrf_failed", message: "The request could not be verified." } }));
    const result = await apiFetch(ORG, "/customers", { method: "POST", body: {} });
    expect(result).toMatchObject({ ok: false, error: { kind: "forbidden" } });
    expect(unauthorized).not.toHaveBeenCalled();
  });
});

describe("loginTarget", () => {
  const here = (pathname: string, search = "") => ({ pathname, search });

  it("keeps the organization page as the way back for the real login", () => {
    expect(loginTarget("/login", here(`/o/${ORG}/customers`, "?page=2"))).toBe(`/login?next=${encodeURIComponent(`/o/${ORG}/customers?page=2`)}`);
  });

  it("offers no way back from anywhere else (the login pages cannot loop), and leaves the dev login alone", () => {
    expect(loginTarget("/login", here("/login"))).toBe("/login");
    expect(loginTarget("/login", here("/setup"))).toBe("/login");
    expect(loginTarget("/login", here("/"))).toBe("/login");
    expect(loginTarget("/dev-login", here(`/o/${ORG}`))).toBe("/dev-login");
  });

  it.each(["https://evil.example/login", "//evil.example", "/\\evil.example", "login", ""])("refuses to navigate to %j and uses /login", (named) => {
    expect(loginTarget(named, here(`/o/${ORG}`))).toBe("/login");
  });
});
