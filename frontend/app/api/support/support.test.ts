// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

/** The support box's door: same-origin only, bounded, public (no sign-in needed), and only a ticket number or a plain refusal comes back. */

const ORIGIN = "http://localhost:3100";
const GOOD = { name: "Anna", email: "anna@example.test", message: "I need help with a price.", website: "" };

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "dev");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function post(body: string, headers: Record<string, string> = {}) {
  return POST(
    new NextRequest(`${ORIGIN}/api/support`, {
      method: "POST",
      body,
      headers: { "content-type": "application/json", origin: ORIGIN, "content-length": String(Buffer.byteLength(body)), ...headers },
    }),
  );
}

const upstream = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

describe("the support box", () => {
  it("passes the message on without any sign-in and answers with the ticket number only", async () => {
    fetchMock.mockResolvedValue(upstream(201, { reference: "SUP-K7M2Q9XD", internal: "never relayed", email_status: "sent" }));

    const response = await post(JSON.stringify(GOOD));

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ reference: "SUP-K7M2Q9XD" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Headers }];
    expect(url).toBe("http://backend.test:8000/api/support");
    expect(JSON.parse(String(init.body))).toEqual(GOOD);
    expect(init.headers.get("authorization")).toBeNull(); // nobody was signed in
  });

  it("refuses another site, a non-JSON body and a body that is too large before the backend is asked", async () => {
    expect((await post(JSON.stringify(GOOD), { origin: "https://evil.example" })).status).toBe(403);
    expect((await post(JSON.stringify(GOOD), { "content-type": "text/plain" })).status).toBe(415);
    expect((await post(JSON.stringify({ ...GOOD, message: "x".repeat(50_000) }))).status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("relays a plain refusal and the wait time, but never what else the backend says", async () => {
    fetchMock.mockResolvedValueOnce(upstream(429, { detail: { code: "rate_limited", message: "Wait a while." } }, { "retry-after": "3600" }));
    const slow = await post(JSON.stringify(GOOD));
    expect(slow.status).toBe(429);
    expect(slow.headers.get("retry-after")).toBe("3600");
    expect((await slow.json()).detail.message).toBe("Wait a while.");

    fetchMock.mockResolvedValueOnce(upstream(422, { detail: [{ loc: ["body", "email"], msg: "Enter an email address we can answer", type: "value_error" }] }));
    expect((await post(JSON.stringify({ ...GOOD, email: "x" }))).status).toBe(422);
  });

  it("answers a fixed 502 when the backend breaks, or sends something that is not a ticket number", async () => {
    fetchMock.mockResolvedValueOnce(upstream(500, { detail: "stack trace with secrets" }));
    const broken = await post(JSON.stringify(GOOD));
    expect(broken.status).toBeGreaterThanOrEqual(502);
    expect(await broken.text()).not.toContain("secrets");

    fetchMock.mockResolvedValueOnce(upstream(201, { reference: "<script>" }));
    expect((await post(JSON.stringify(GOOD))).status).toBe(502);

    fetchMock.mockResolvedValueOnce(upstream(401, { detail: "Not authenticated" }));
    const unauthenticated = await post(JSON.stringify(GOOD));
    expect(unauthenticated.status).toBe(502); // a public box never bounces a visitor to the login page
  });
});
