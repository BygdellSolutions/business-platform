import { expect, test } from "../fixtures";

import { E2E_PASSWORD, ensureCredential, preAuthToken, signInThroughPage } from "../auth-support";
import { BACKEND_URL, BASE_URL } from "../env";
import { FREDRIK, MARIA, ORG_A, ORG_B, createWorld, sql, testRow, type World } from "../support";

/**
 * Real login in a real browser: the page, the generic failure, the cookies, what the browser can and cannot see,
 * and where it goes afterwards.
 */

let world: World | undefined;
test.afterEach(() => {
  world?.cleanup();
  world = undefined;
});

const cookieOf = async (context: import("@playwright/test").BrowserContext, name: string) => (await context.cookies()).find((cookie) => cookie.name === name);

test.describe("who gets in, and who is sent to /login", () => {
  test("an unauthenticated browser is sent to /login from every application page, with a safe way back", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);

    await page.goto(`/o/${ORG_A.id}/customers`);
    await expect(page).toHaveURL(`${BASE_URL}/login?next=${encodeURIComponent(`/o/${ORG_A.id}`)}`);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("a real login reaches the application and the user sees who they are, from the backend", async ({ page }) => {
    await signInThroughPage(page, FREDRIK);

    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
    await expect(page.getByTestId("user-email")).toHaveText(FREDRIK);
    await expect(page.getByTestId("organization-list").getByRole("link")).toHaveText([ORG_A.name, ORG_B.name]);
  });

  test("a user with one organization goes straight to it, with the role they hold there", async ({ page }) => {
    await signInThroughPage(page, MARIA);

    await expect(page).toHaveURL(new RegExp(`/o/${ORG_B.id}$`));
    await expect(page.getByTestId("org-role")).toHaveText("employee");
  });

  test("the dev sign-in does not exist and gives no identity", async ({ page, context }) => {
    const response = await page.goto("/dev-login");
    expect(response?.status()).toBe(404);
    const post = await context.request.post("/api/dev-session", { form: { email: FREDRIK }, headers: { origin: BASE_URL }, maxRedirects: 0 });
    expect(post.status()).toBe(404);
    expect((await context.cookies()).map((c) => c.name)).not.toContain("bp_dev_user");
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
  });
});

test.describe("failure is one generic message", () => {
  test("an unknown email, a wrong password, a user with no password and a disabled user are indistinguishable", async ({ page, context }) => {
    world = createWorld({ label: "LoginFailures" });
    await ensureCredential(world.email);
    const noPassword = world.addMember("viewer"); // exists, has NO credential
    const disabled = world.addMember("employee");
    await ensureCredential(disabled);
    testRow(`update users set is_active = false where email = ${sql(disabled)}`);

    const attempts: [string, string][] = [
      ["nobody@example.test", E2E_PASSWORD],
      [world.email, "the wrong long password"],
      [noPassword, E2E_PASSWORD],
      [disabled, E2E_PASSWORD],
    ];
    const outcomes: string[] = [];
    for (const [email, password] of attempts) {
      await page.goto("/login");
      await expect(page.getByTestId("login-form")).toHaveAttribute("data-ready", "true");
      await page.getByLabel("Email").fill(email);
      await page.getByLabel("Password").fill(password);
      await page.getByTestId("login-submit").click();
      await expect(page.getByTestId("login-error")).toBeVisible();
      outcomes.push(`${await page.getByTestId("login-error").innerText()}|${page.url()}|${(await context.cookies()).map((c) => c.name).filter((n) => n !== "bp_pre").join(",")}`);
    }

    expect(new Set(outcomes).size).toBe(1);
    expect(outcomes[0]).toBe(`Invalid email or password.|${BASE_URL}/login|`);
  });

  test("the same answer comes from the BFF, with no cookie, and never says why", async ({ context }) => {
    const secret = await preAuthToken(context);
    const bodies: string[] = [];
    for (const email of ["nobody@example.test", FREDRIK]) {
      const response = await context.request.post("/api/auth/login", { headers: { origin: BASE_URL, "x-pre-auth": secret }, data: { email, password: "wrong wrong wrong wrong" } });
      expect(response.status()).toBe(401);
      expect(response.headers()["set-cookie"] ?? "").not.toContain("bp_session");
      bodies.push(await response.text());
    }
    expect(bodies[0]).toBe(bodies[1]);
    expect(bodies[0]).toBe('{"detail":"Invalid email or password"}');
  });
});

test.describe("the cookies", () => {
  test("the session cookie is HttpOnly, the CSRF cookie is readable, both are Lax, Path=/ and host-only, and no cookie holds an identity", async ({ page, context }) => {
    await signInThroughPage(page, FREDRIK);
    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();

    const session = await cookieOf(context, "bp_session");
    const csrf = await cookieOf(context, "bp_csrf");
    expect(session).toMatchObject({ httpOnly: true, sameSite: "Lax", path: "/", secure: false }); // plain http in the e2e run; the https names and Secure are covered by unit tests
    expect(csrf).toMatchObject({ httpOnly: false, sameSite: "Lax", path: "/" });
    expect(session!.domain).toBe("127.0.0.1"); // host-only (no leading dot)
    expect(session!.expires).toBeGreaterThan(Date.now() / 1000);
    const names = (await context.cookies()).map((c) => c.name).sort();
    expect(names).toEqual(["bp_csrf", "bp_session"]); // the pre-auth cookie was consumed; no dev cookie, no email cookie
  });

  test("page scripts cannot read the session token, and it is nowhere in the page, the URL, any storage or any response header", async ({ page, context }) => {
    const seen: string[] = [];
    page.on("response", (response) => seen.push(JSON.stringify(response.headers())));
    await signInThroughPage(page, FREDRIK);
    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
    const token = (await cookieOf(context, "bp_session"))!.value;
    const csrfToken = (await cookieOf(context, "bp_csrf"))!.value;

    await page.getByRole("link", { name: ORG_A.name }).click();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);

    const visible = await page.evaluate(() => ({
      cookie: document.cookie,
      local: JSON.stringify(Object.entries(localStorage)),
      session: JSON.stringify(Object.entries(sessionStorage)),
      html: document.documentElement.outerHTML,
      url: location.href,
      history: history.length,
    }));
    expect(visible.cookie).toContain(csrfToken); // the CSRF token is readable on purpose ...
    expect(visible.cookie).not.toContain(token); // ... the session token is not
    expect(visible.cookie).not.toContain("bp_session");
    for (const where of [visible.local, visible.session, visible.html, visible.url]) expect(where).not.toContain(token);
    expect(visible.local).toBe("[]");
    expect(visible.session).toBe("[]");
    expect(seen.join("\n")).not.toContain(token);
  });

  test("the login response carries the cookies as Set-Cookie only: no token in the body or in any readable header", async ({ context }) => {
    await ensureCredential(FREDRIK);
    const secret = await preAuthToken(context);

    const response = await context.request.post("/api/auth/login", { headers: { origin: BASE_URL, "x-pre-auth": secret }, data: { email: FREDRIK, password: E2E_PASSWORD, next: `/o/${ORG_A.id}` } });

    expect(response.status()).toBe(200);
    const token = (await cookieOf(context, "bp_session"))!.value;
    expect(await response.json()).toEqual({ next: `/o/${ORG_A.id}` });
    const readable = Object.entries(response.headers()).filter(([name]) => name !== "set-cookie");
    expect(JSON.stringify(readable)).not.toContain(token);
    expect(Object.keys(response.headers()).filter((name) => name.startsWith("x-middleware"))).toEqual([]);
    expect(response.headers()["cache-control"]).toBe("no-store");
  });

  test("login and setup pages are served uncached and with no referrer", async ({ request }) => {
    for (const path of ["/login", "/setup"]) {
      const response = await request.get(path);
      expect(response.status()).toBe(200);
      expect(response.headers()["cache-control"]).toBe("no-store");
      expect(response.headers()["referrer-policy"]).toBe("no-referrer");
    }
    const pre = await request.get("/api/auth/pre");
    expect(pre.headers()["cache-control"]).toBe("no-store");
  });

  test("a session cookie planted before login is never adopted: the login issues a new one and the planted value is dead", async ({ page, context }) => {
    const planted = "F".repeat(43);
    await context.addCookies([{ name: "bp_session", value: planted, url: BASE_URL, httpOnly: true, sameSite: "Lax" }]);

    await signInThroughPage(page, FREDRIK);
    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();

    const fresh = (await cookieOf(context, "bp_session"))!.value;
    expect(fresh).not.toBe(planted);
    expect((await fetch(`${BACKEND_URL}/api/me/organizations`, { headers: { authorization: `Bearer ${planted}` } })).status).toBe(401);
    expect((await fetch(`${BACKEND_URL}/api/me/organizations`, { headers: { authorization: `Bearer ${fresh}` } })).status).toBe(200);
  });
});

test.describe("where login leads (no open redirects)", () => {
  test("a safe relative destination is used after login", async ({ page }) => {
    await signInThroughPage(page, FREDRIK, E2E_PASSWORD, `/o/${ORG_A.id}/customers`);
    await expect(page).toHaveURL(`${BASE_URL}/o/${ORG_A.id}/customers`);
    await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
  });

  const HOSTILE = [
    "https://evil.example/",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    `/o/${ORG_A.id}/%2e%2e/%2e%2e/`,
    `/o/${ORG_A.id}/../../evil`,
    "/%2F%2Fevil.example",
    "/login?next=https://evil.example",
    "/api/auth/logout",
  ];
  for (const next of HOSTILE) {
    test(`a hostile destination (${next}) ends on this site's home, never elsewhere`, async ({ page }) => {
      await signInThroughPage(page, FREDRIK, E2E_PASSWORD, next);
      await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
      expect(new URL(page.url()).origin).toBe(BASE_URL);
      expect(new URL(page.url()).pathname).toBe("/");
    });
  }

  test("a stale or planted next parameter on the login page itself is validated again", async ({ page }) => {
    await ensureCredential(FREDRIK);
    await page.goto(`/login?next=${encodeURIComponent("https://evil.example/")}`);
    await expect(page.getByTestId("login-form")).toHaveAttribute("data-ready", "true");
    await page.getByLabel("Email").fill(FREDRIK);
    await page.getByLabel("Password").fill(E2E_PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page).toHaveURL(`${BASE_URL}/`);
  });
});

test.describe("the pre-authentication double-submit", () => {
  test("login without the pre-auth header, with a different one, or with no pre-auth cookie is refused before it reaches FastAPI", async ({ context, playwright }) => {
    await ensureCredential(FREDRIK);
    const secret = await preAuthToken(context);
    const attempt = (headers: Record<string, string>) => context.request.post("/api/auth/login", { headers, data: { email: FREDRIK, password: E2E_PASSWORD } });

    expect((await attempt({ origin: BASE_URL })).status()).toBe(403);
    expect((await attempt({ origin: BASE_URL, "x-pre-auth": "X".repeat(43) })).status()).toBe(403);
    expect((await attempt({ "x-pre-auth": secret })).status()).toBe(403); // no Origin
    expect((await attempt({ origin: "http://evil.example", "x-pre-auth": secret })).status()).toBe(403);

    const bare = await playwright.request.newContext({ baseURL: BASE_URL }); // a browser with no pre-auth cookie
    expect((await bare.post("/api/auth/login", { headers: { origin: BASE_URL, "x-pre-auth": secret }, data: { email: FREDRIK, password: E2E_PASSWORD } })).status()).toBe(403);
    await bare.dispose();

    expect((await attempt({ origin: BASE_URL, "x-pre-auth": secret })).status()).toBe(200);
  });

  test("the pre-auth secret is not a credential: with it alone nothing in the application is reachable", async ({ context }) => {
    const secret = await preAuthToken(context);
    const response = await context.request.get(`/api/o/${ORG_A.id}/customers`, { headers: { authorization: `Bearer ${secret}`, "x-pre-auth": secret } });
    expect(response.status()).toBe(401);
  });
});
