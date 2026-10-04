import { expect, test } from "../fixtures";

import { E2E_PASSWORD, signInThroughPage, signInWithPassword } from "../auth-support";
import { BACKEND_URL, BASE_URL } from "../env";
import { FREDRIK, MARIA, ORG_A, ORG_B, createWorld, sql, testRow, type World } from "../support";

/**
 * A session ends in several ways (logout, revocation, expiry, a disabled user). The browser must move to the
 * login rather than show a business error, keep a safe way back, never loop, and must not mistake a 403 (role)
 * or a 404 (tenant isolation) for a lost session.
 */

let world: World | undefined;
test.afterEach(() => {
  world?.cleanup();
  world = undefined;
});

const SESSIONS_OF = (email: string) => `user_id = (select id from users where email = ${sql(email)})`;
const tokenOf = async (context: import("@playwright/test").BrowserContext) => (await context.cookies()).find((c) => c.name === "bp_session")!.value;
const backendStatus = async (token: string, path = "/api/me/user") => (await fetch(`${BACKEND_URL}${path}`, { headers: { authorization: `Bearer ${token}` } })).status;

test.describe("logout", () => {
  test("signs this browser out, ends the session at the server, and a copied token cannot be reused", async ({ page, context }) => {
    await signInThroughPage(page, FREDRIK);
    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
    const token = await tokenOf(context);
    expect(await backendStatus(token)).toBe(200); // the token really is a working credential ...

    await page.getByTestId("sign-out").click();

    await expect(page).toHaveURL(`${BASE_URL}/login?notice=signed-out`);
    await expect(page.getByTestId("login-notice")).toHaveText("You have been signed out.");
    expect((await context.cookies()).map((c) => c.name)).not.toContain("bp_session");
    expect((await context.cookies()).map((c) => c.name)).not.toContain("bp_csrf");
    expect(await backendStatus(token)).toBe(401); // ... and after logout it is dead: revoked on the server, not just forgotten
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("from inside an organization too, and the back button does not reveal anything", async ({ page, context }) => {
    await signInThroughPage(page, FREDRIK);
    await page.getByRole("link", { name: ORG_A.name }).click();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);

    await page.getByTestId("sign-out").click();
    await expect(page).toHaveURL(/\/login\?notice=signed-out$/);
    await page.goBack();
    await page.reload();

    await expect(page).toHaveURL(/\/login/); // the organization page cannot be shown from a signed-out browser
    expect((await context.cookies()).some((c) => c.name === "bp_session")).toBe(false);
  });

  test("logout in one browser leaves another browser's session of the same user alone", async ({ browser }) => {
    const first = await browser.newContext({ baseURL: BASE_URL });
    const second = await browser.newContext({ baseURL: BASE_URL });
    await signInWithPassword(first, FREDRIK);
    await signInWithPassword(second, FREDRIK);
    const tokenFirst = await tokenOf(first);
    const tokenSecond = await tokenOf(second);
    expect(tokenFirst).not.toBe(tokenSecond);

    const page = await first.newPage();
    await page.goto("/");
    await page.getByTestId("sign-out").click();
    await expect(page).toHaveURL(/\/login/);

    expect(await backendStatus(tokenFirst)).toBe(401);
    expect(await backendStatus(tokenSecond)).toBe(200);
    await first.close();
    await second.close();
  });

  test("when the server cannot confirm, the browser is signed out anyway and the login page says so honestly", async ({ page, context }) => {
    await signInThroughPage(page, FREDRIK);
    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
    await page.route("**/api/auth/logout", async (route) => {
      // The BFF really runs (it clears the cookies), but the answer to the browser is lost.
      await route.fetch();
      await route.abort("connectionreset");
    });

    await page.getByTestId("sign-out").click();

    await expect(page).toHaveURL(`${BASE_URL}/login?notice=signed-out-unconfirmed`);
    await expect(page.getByTestId("login-notice")).toContainText("could not confirm");
    void context;
  });

  test("is itself CSRF-protected: a cross-site or token-less logout does nothing", async ({ page, context, playwright }) => {
    await signInThroughPage(page, FREDRIK);
    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
    const token = await tokenOf(context);
    const raw = await playwright.request.newContext({ baseURL: BASE_URL, storageState: await context.storageState() });

    expect((await raw.post("/api/auth/logout")).status()).toBe(403); // no Origin, no CSRF header
    expect((await raw.post("/api/auth/logout", { headers: { origin: "http://evil.example" } })).status()).toBe(403);
    expect((await raw.post("/api/auth/logout", { headers: { origin: BASE_URL } })).status()).toBe(403); // no CSRF header
    expect((await raw.post("/api/auth/logout", { headers: { origin: BASE_URL, "x-csrf-token": "X".repeat(43) } })).status()).toBe(403);

    expect(await backendStatus(token)).toBe(200); // still signed in everywhere
    await page.reload();
    await expect(page.getByTestId("user-email")).toHaveText(FREDRIK);
    await raw.dispose();
  });
});

test.describe("a session that ends elsewhere sends the browser to the login, with a way back", () => {
  const ENDINGS: [string, (email: string) => void][] = [
    ["revoked", (email) => testRow(`update auth_sessions set revoked_at = now(), revoked_reason = 'logout' where ${SESSIONS_OF(email)}`)],
    ["absolutely expired", (email) => testRow(`update auth_sessions set absolute_expires_at = now() - interval '1 minute' where ${SESSIONS_OF(email)}`)],
    ["idle for too long", (email) => testRow(`update auth_sessions set last_used_at = now() - interval '13 hours' where ${SESSIONS_OF(email)}`)],
    ["the user was disabled", (email) => testRow(`update users set is_active = false where email = ${sql(email)}`)],
  ];

  for (const [name, end] of ENDINGS) {
    test(`(${name}) a page load goes to /login with the page as the way back, then returns there after login`, async ({ page }) => {
      world = createWorld({ label: "EndedSession" });
      testRow(`update organizations set default_currency = 'SEK' where id = ${sql(world.orgId)}`);
      await signInThroughPage(page, world.email);
      await page.goto(`/o/${world.orgId}/customers`);
      await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();

      end(world.email);
      await page.reload();

      await expect(page).toHaveURL(`${BASE_URL}/login?next=${encodeURIComponent(`/o/${world.orgId}`)}`);
      await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
      await expect(page.getByTestId("login-error")).toHaveCount(0); // no loop, no business error
      if (name === "the user was disabled") return;
      await page.getByLabel("Email").fill(world.email);
      await page.getByLabel("Password").fill(E2E_PASSWORD);
      await page.getByTestId("login-submit").click();
      await expect(page).toHaveURL(`${BASE_URL}/o/${world.orgId}`);
    });

    test(`(${name}) a request from an open page goes to /login, not to a business error`, async ({ page }) => {
      world = createWorld({ label: "EndedSessionClient" });
      await signInThroughPage(page, world.email);
      await page.goto(`/o/${world.orgId}/customers/new`);
      await expect(page.getByRole("heading", { name: "New customer" })).toBeVisible();
      await page.getByLabel("Name", { exact: true }).fill("Written after the session ended");

      end(world.email);
      await page.getByTestId("submit").click();

      await expect(page).toHaveURL(`${BASE_URL}/login?next=${encodeURIComponent(`/o/${world.orgId}/customers/new`)}`);
      expect(testRow(`select count(*) from customers where organization_id = ${sql(world.orgId)} and name = 'Written after the session ended'`)).toBe("0");
    });
  }
});

test.describe("what is NOT a lost session", () => {
  test("a 403 (the role does not allow it) is shown as such and the user stays signed in", async ({ page, context }) => {
    await signInThroughPage(page, MARIA); // an employee of ORG_B: may not change organization settings
    await expect(page).toHaveURL(new RegExp(`/o/${ORG_B.id}$`));

    const response = await context.request.patch(`/api/o/${ORG_B.id}/organization`, { data: { name: "Hijacked" } });

    expect(response.status()).toBe(403);
    expect(testRow(`select name from organizations where id = ${sql(ORG_B.id)}`)).toBe(ORG_B.name);
    await page.reload();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name); // still signed in
    expect(await tokenOf(context)).toBeTruthy();
  });

  test("a 404 (not your organization) is the same not-found page as for a random id, not the login", async ({ page }) => {
    await signInThroughPage(page, MARIA);
    const foreign = await page.goto(`/o/${ORG_A.id}`);
    const foreignText = await page.locator("body").innerText();
    const random = await page.goto("/o/00000000-0000-4000-8000-00000000dead");

    expect(foreign?.status()).toBe(404);
    expect(random?.status()).toBe(404);
    await expect(page.getByTestId("not-found")).toBeVisible();
    expect(await page.locator("body").innerText()).toBe(foreignText);
    expect(page.url()).not.toContain("/login");
  });

  test("a read-only role reads, and its forbidden mutation does not send it to the login", async ({ page, context }) => {
    world = createWorld({ label: "ViewerSession" });
    const viewer = world.addMember("viewer");
    await signInThroughPage(page, viewer);
    await page.goto(`/o/${world.orgId}/customers`);
    await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();

    const created = await context.request.post(`/api/o/${world.orgId}/customers`, { data: { customer_type: "person", name: "Viewer wrote this" } });

    // (Customers are open to every member in this application; the point is that whatever the answer, it is not 401.)
    expect(created.status()).not.toBe(401);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
  });
});

test.describe("two people, two tabs", () => {
  test("two browsers signed in as different users each see only their own identity and organizations", async ({ browser }) => {
    const a = await browser.newContext({ baseURL: BASE_URL });
    const b = await browser.newContext({ baseURL: BASE_URL });
    await signInWithPassword(a, FREDRIK);
    await signInWithPassword(b, MARIA);
    const pageA = await a.newPage();
    const pageB = await b.newPage();

    await pageA.goto("/");
    await pageB.goto("/");

    await expect(pageA.getByTestId("user-email")).toHaveText(FREDRIK);
    await expect(pageA.getByTestId("organization-list").getByRole("link")).toHaveText([ORG_A.name, ORG_B.name]);
    await expect(pageB).toHaveURL(new RegExp(`/o/${ORG_B.id}$`)); // Maria has one organization
    await expect(pageB.getByTestId("user-email")).toHaveText(MARIA);
    const foreignForMaria = await pageB.goto(`/o/${ORG_A.id}`);
    expect(foreignForMaria?.status()).toBe(404);
    await a.close();
    await b.close();
  });

  test("two tabs of one session work in different organizations at once", async ({ context, page }) => {
    await signInThroughPage(page, FREDRIK);
    const tabA = await context.newPage();
    const tabB = await context.newPage();

    await tabA.goto(`/o/${ORG_A.id}`);
    await tabB.goto(`/o/${ORG_B.id}`);

    await expect(tabA.getByTestId("org-name")).toHaveText(ORG_A.name);
    await expect(tabB.getByTestId("org-name")).toHaveText(ORG_B.name);
    await expect(tabA.getByTestId("org-role")).toHaveText("owner");
    await expect(tabB.getByTestId("org-role")).toHaveText("admin");
    expect((await context.cookies()).filter((c) => c.name.includes("org"))).toEqual([]); // no active-organization cookie exists
  });
});
