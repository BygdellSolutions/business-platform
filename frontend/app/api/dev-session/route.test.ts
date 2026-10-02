// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

const ORIGIN = "http://localhost:3100";
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("DEV_IDENTITY", "enabled");
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  fetchMock = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function post(fields: Record<string, string>, headers: Record<string, string> = {}) {
  return POST(
    new NextRequest(`${ORIGIN}/api/dev-session`, {
      method: "POST",
      body: new URLSearchParams(fields),
      headers: { "content-type": "application/x-www-form-urlencoded", ...headers },
    }),
  );
}

describe("signing in", () => {
  it("sets an httpOnly, same-site cookie for a user the backend knows, then goes home", async () => {
    const response = await post({ email: "Maria@Dev.Test" });

    expect(response.status).toBe(303);
    expect(new URL(response.headers.get("location")!, ORIGIN).pathname).toBe("/");
    const cookie = response.cookies.get("bp_dev_user");
    expect(cookie).toMatchObject({ value: "maria@dev.test", httpOnly: true, sameSite: "lax", path: "/" });
    expect(cookie?.maxAge).toBeGreaterThan(0);
  });

  it("checks the user with the backend and sends no organization", async () => {
    await post({ email: "maria@dev.test" });

    const [url, init] = fetchMock.mock.calls[0] as [string, { headers: Headers }];
    expect(url).toBe("http://backend.test:8000/api/me/organizations");
    expect(init.headers.get("x-dev-user-email")).toBe("maria@dev.test");
    expect(init.headers.has("x-organization-id")).toBe(false);
  });

  it("refuses a user the backend does not know, without setting a cookie", async () => {
    fetchMock.mockResolvedValue(new Response('{"detail":"Not authenticated"}', { status: 401 }));

    const response = await post({ email: "nobody@dev.test" });

    expect(new URL(response.headers.get("location")!, ORIGIN).search).toBe("?error=unknown");
    expect(response.cookies.get("bp_dev_user")).toBeUndefined();
  });

  it.each(["", "not-an-email", "a b@c.test"])("refuses %j before asking the backend", async (email) => {
    const response = await post({ email });

    expect(new URL(response.headers.get("location")!, ORIGIN).search).toBe("?error=invalid");
    expect(response.cookies.get("bp_dev_user")).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports an unreachable or failing backend without setting a cookie", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    expect(new URL((await post({ email: "maria@dev.test" })).headers.get("location")!, ORIGIN).search).toBe("?error=unavailable");

    fetchMock.mockResolvedValue(new Response("boom", { status: 500 }));
    const response = await post({ email: "maria@dev.test" });
    expect(new URL(response.headers.get("location")!, ORIGIN).search).toBe("?error=unavailable");
    expect(response.cookies.get("bp_dev_user")).toBeUndefined();
  });
});

describe("signing out", () => {
  it("clears the cookie and returns to the sign-in page", async () => {
    const response = await post({ logout: "1" });

    expect(response.status).toBe(303);
    expect(new URL(response.headers.get("location")!, ORIGIN).pathname).toBe("/dev-login");
    expect(response.cookies.get("bp_dev_user")).toMatchObject({ value: "" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("guards", () => {
  it("does not exist unless DEV_IDENTITY=enabled", async () => {
    vi.stubEnv("DEV_IDENTITY", "");

    const response = await post({ email: "maria@dev.test" });

    expect(response.status).toBe(404);
    expect(response.cookies.get("bp_dev_user")).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a cross-origin form post", async () => {
    const response = await post({ email: "maria@dev.test" }, { origin: "http://evil.test" });

    expect(response.status).toBe(403);
    expect(response.cookies.get("bp_dev_user")).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts the same origin", async () => {
    expect((await post({ email: "maria@dev.test" }, { origin: ORIGIN })).status).toBe(303);
  });
});

describe("redirects", () => {
  it("are relative, so the browser stays on the host that received the cookie", async () => {
    const cases: Record<string, string>[] = [{ email: "maria@dev.test" }, { logout: "1" }, { email: "nobody" }];
    for (const fields of cases) {
      const location = (await post(fields)).headers.get("location")!;
      expect(location.startsWith("/")).toBe(true);
      expect(location).not.toContain("://");
    }
  });
});
