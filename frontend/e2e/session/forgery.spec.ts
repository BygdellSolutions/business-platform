import { expect, test } from "../fixtures";

import { csrfCookie, direct, installBrowserHeaders, signInWithPassword } from "../auth-support";
import { BACKEND_URL, BASE_URL } from "../env";
import { FREDRIK, MARIA, ORG_A, ORG_B, RANDOM_ORG, bffUrl, sql, testRow, unique } from "../support";

/**
 * The browser can assert nothing about who it is, which organization it acts in, or what role it has. Each layer
 * is proved on its own, through the real BFF and the real FastAPI:
 *
 *   BFF:     Origin against PUBLIC_ORIGIN, the CSRF cookie/header pair, header stripping
 *   FastAPI: the session, the session-bound CSRF token, the organization selector against the membership
 */

const customersOf = (org: string) => testRow(`select count(*) from customers where organization_id = ${sql(org)}`);

async function rawContext(playwright: import("@playwright/test").PlaywrightWorkerArgs["playwright"], context: import("@playwright/test").BrowserContext) {
  // An API client with the browser's cookies but WITHOUT the helpers that add Origin and CSRF: what an attacker's request looks like.
  return playwright.request.newContext({ baseURL: BASE_URL, storageState: await context.storageState() });
}

test.describe("CSRF: both layers, independently", () => {
  test("a mutation with the right Origin and a matching CSRF pair succeeds", async ({ context }) => {
    await signInWithPassword(context, FREDRIK);
    const name = unique("Csrf OK");

    const response = await context.request.post(bffUrl(ORG_A.id, "/customers"), { data: { customer_type: "person", name } });

    expect(response.status()).toBe(201);
    expect(testRow(`select count(*) from customers where name = ${sql(name)}`)).toBe("1");
  });

  test("BFF layer: a missing or wrong Origin, a missing or wrong CSRF header, or no CSRF cookie are refused and change nothing", async ({ context, playwright }) => {
    await signInWithPassword(context, FREDRIK);
    const csrf = (await csrfCookie(context))!;
    const raw = await rawContext(playwright, context);
    const before = customersOf(ORG_A.id);
    const create = (headers: Record<string, string>, name: string) => raw.post(bffUrl(ORG_A.id, "/customers"), { headers, data: { customer_type: "person", name } });

    expect((await create({ "x-csrf-token": csrf }, "no origin")).status()).toBe(403);
    expect((await create({ origin: "http://evil.example", "x-csrf-token": csrf }, "evil origin")).status()).toBe(403);
    expect((await create({ origin: "http://127.0.0.1:3199", "x-csrf-token": csrf }, "other port")).status()).toBe(403);
    expect((await create({ origin: BASE_URL }, "no csrf header")).status()).toBe(403);
    expect((await create({ origin: BASE_URL, "x-csrf-token": "X".repeat(43) }, "wrong csrf header")).status()).toBe(403);
    expect((await create({ origin: BASE_URL, "x-csrf-token": csrf.slice(0, 20) }, "short csrf header")).status()).toBe(403);
    expect(customersOf(ORG_A.id)).toBe(before);

    const noCookie = await playwright.request.newContext({ baseURL: BASE_URL, storageState: { cookies: (await context.storageState()).cookies.filter((c) => c.name !== "bp_csrf"), origins: [] } });
    expect((await noCookie.post(bffUrl(ORG_A.id, "/customers"), { headers: { origin: BASE_URL, "x-csrf-token": csrf }, data: { customer_type: "person", name: "no csrf cookie" } })).status()).toBe(403);
    expect(customersOf(ORG_A.id)).toBe(before);
    await noCookie.dispose();
    await raw.dispose();
  });

  test("BFF layer: the Origin is judged against PUBLIC_ORIGIN, not against the Host header", async ({ context, playwright }) => {
    await signInWithPassword(context, FREDRIK);
    const csrf = (await csrfCookie(context))!;
    const raw = await rawContext(playwright, context);
    const create = (headers: Record<string, string>) => raw.post(bffUrl(ORG_A.id, "/customers"), { headers: { "x-csrf-token": csrf, ...headers }, data: { customer_type: "person", name: unique("Origin vs Host") } });

    // A foreign Origin is refused even when the request claims that same host ...
    expect((await create({ origin: "http://evil.example", host: "evil.example", "x-forwarded-host": "evil.example" })).status()).toBe(403);
    // ... and the canonical Origin is what counts, whatever Host or forwarded host is claimed.
    expect((await create({ origin: BASE_URL, "x-forwarded-host": "evil.example" })).status()).toBe(201);
    await raw.dispose();
  });

  test("FastAPI layer: a token pair the BFF accepts (cookie and header equal) but that is not THIS session's is still refused", async ({ context }) => {
    await signInWithPassword(context, FREDRIK);
    const forged = "Z".repeat(43);
    // This is what cookie tossing from a sibling domain would try: set the CSRF cookie AND send the same value in the header.
    await context.addCookies([{ name: "bp_csrf", value: forged, url: BASE_URL, sameSite: "Lax" }]);
    const before = customersOf(ORG_A.id);

    const response = await context.request.post(bffUrl(ORG_A.id, "/customers"), { headers: { "x-csrf-token": forged }, data: { customer_type: "person", name: "tossed" } });

    expect(response.status()).toBe(403); // the BFF's double-submit check passed; FastAPI's session-bound check did not
    expect(((await response.json()) as { detail: { code: string } }).detail.code).toBe("csrf_failed");
    expect(customersOf(ORG_A.id)).toBe(before);
  });

  test("FastAPI layer: another user's valid CSRF token is no use with this session", async ({ browser }) => {
    const a = await browser.newContext({ baseURL: BASE_URL });
    const b = await browser.newContext({ baseURL: BASE_URL });
    await signInWithPassword(a, FREDRIK);
    await signInWithPassword(b, MARIA);
    const csrfOfMaria = (await csrfCookie(b))!;
    await a.addCookies([{ name: "bp_csrf", value: csrfOfMaria, url: BASE_URL, sameSite: "Lax" }]);

    const response = await a.request.post(bffUrl(ORG_A.id, "/customers"), { headers: { "x-csrf-token": csrfOfMaria }, data: { customer_type: "person", name: "borrowed csrf" } });

    expect(response.status()).toBe(403);
    await a.close();
    await b.close();
  });

  test("reads need neither Origin nor a CSRF token", async ({ context, playwright }) => {
    await signInWithPassword(context, FREDRIK);
    const raw = await rawContext(playwright, context);
    expect((await raw.get(bffUrl(ORG_A.id, "/customers"))).status()).toBe(200);
    await raw.dispose();
  });

  test("the application's own page sends the CSRF header on its own: creating a customer in the browser works", async ({ page, context }) => {
    await signInWithPassword(context, FREDRIK);
    installBrowserHeaders(context);
    const name = unique("From the page");
    await page.goto(`/o/${ORG_A.id}/customers/new`);

    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("created")).toBeVisible();
    expect(testRow(`select count(*) from customers where name = ${sql(name)}`)).toBe("1");
  });
});

test.describe("what the browser cannot assert", () => {
  test("a forged Authorization header is ignored: the cookie's session decides who the request is", async ({ context }) => {
    const other = await (await import("../auth-support")).preAuthToken(context); // any well-formed token
    await signInWithPassword(context, MARIA);

    const me = await context.request.get(bffUrl(ORG_B.id, "/me/user"), { headers: { authorization: `Bearer ${other}` } });

    expect(me.status()).toBe(200);
    expect(((await me.json()) as { email: string }).email).toBe(MARIA);
  });

  test("another user's real session token in Authorization changes nothing either", async ({ browser }) => {
    const fredrik = await browser.newContext({ baseURL: BASE_URL });
    const maria = await browser.newContext({ baseURL: BASE_URL });
    await signInWithPassword(fredrik, FREDRIK);
    await signInWithPassword(maria, MARIA);
    const fredriksToken = (await fredrik.cookies()).find((c) => c.name === "bp_session")!.value;
    expect((await direct(`${BACKEND_URL}/api/me/user`, { headers: { authorization: `Bearer ${fredriksToken}` } })).status).toBe(200); // a real credential ...

    const me = await maria.request.get(bffUrl(ORG_B.id, "/me/user"), { headers: { authorization: `Bearer ${fredriksToken}` } });

    expect(((await me.json()) as { email: string }).email).toBe(MARIA); // ... that the BFF never forwards
    await fredrik.close();
    await maria.close();
  });

  test("a forged dev-identity header and the dev cookie give nothing in session mode (even though the backend has a DEV_USER_EMAIL)", async ({ context, playwright }) => {
    const anonymous = await playwright.request.newContext({ baseURL: BASE_URL });
    expect((await anonymous.get(bffUrl(ORG_A.id, "/customers"), { headers: { "x-dev-user-email": FREDRIK } })).status()).toBe(401);
    await context.addCookies([{ name: "bp_dev_user", value: FREDRIK, url: BASE_URL }]);
    expect((await context.request.get(bffUrl(ORG_A.id, "/customers"), { headers: { "x-dev-user-email": FREDRIK } })).status()).toBe(401);
    await anonymous.dispose();

    // The backend itself, asked directly: no header, the dev header, and the configured DEV_USER_EMAIL all identify nobody.
    expect((await direct(`${BACKEND_URL}/api/me/organizations`)).status).toBe(401);
    expect((await direct(`${BACKEND_URL}/api/me/organizations`, { headers: { "x-dev-user-email": FREDRIK } })).status).toBe(401);
  });

  test("a failed session does not fall back to the dev identity", async ({ context }) => {
    await context.addCookies([
      { name: "bp_session", value: "D".repeat(43), url: BASE_URL, httpOnly: true, sameSite: "Lax" },
      { name: "bp_dev_user", value: FREDRIK, url: BASE_URL },
    ]);
    const response = await context.request.get(bffUrl(ORG_A.id, "/customers"), { headers: { "x-dev-user-email": FREDRIK } });
    expect(response.status()).toBe(401);
    expect((await direct(`${BACKEND_URL}/api/me/organizations`, { headers: { authorization: `Bearer ${"D".repeat(43)}`, "x-dev-user-email": FREDRIK } })).status).toBe(401);
  });

  test("a forged organization header is replaced by the one in the URL, and a foreign organization stays a 404", async ({ context }) => {
    await signInWithPassword(context, MARIA); // employee of ORG_B only
    const own = await context.request.get(bffUrl(ORG_B.id, "/customers"));
    const withForged = await context.request.get(bffUrl(ORG_B.id, "/customers"), { headers: { "x-organization-id": ORG_A.id } });

    expect(withForged.status()).toBe(200);
    expect(await withForged.text()).toBe(await own.text()); // ORG_B's data, not ORG_A's

    const foreign = await context.request.get(bffUrl(ORG_A.id, "/customers"), { headers: { "x-organization-id": ORG_B.id } });
    const random = await context.request.get(bffUrl(RANDOM_ORG, "/customers"), { headers: { "x-organization-id": ORG_B.id } });
    expect([foreign.status(), random.status()]).toEqual([404, 404]);
    expect(await foreign.text()).toBe(await random.text());
  });

  test("a forged role or permission header does not change what a role may do", async ({ context }) => {
    await signInWithPassword(context, MARIA); // employee

    const response = await context.request.patch(bffUrl(ORG_B.id, "/organization"), {
      headers: { "x-user-role": "owner", "x-role": "owner", "x-permissions": "organization:write", "x-forwarded-user": FREDRIK },
      data: { name: "Hijacked" },
    });

    expect(response.status()).toBe(403);
    expect(testRow(`select name from organizations where id = ${sql(ORG_B.id)}`)).toBe(ORG_B.name);
  });

  test("a forged proxy header cannot reach FastAPI as a client address", async ({ playwright }) => {
    const anonymous = await playwright.request.newContext({ baseURL: BASE_URL });
    const pre = await anonymous.get("/api/auth/pre");
    const secret = ((await pre.json()) as { token: string }).token;
    // Hundreds of failed logins "from" a spoofed address would bury a victim's address if the header were trusted.
    for (let attempt = 0; attempt < 3; attempt++) {
      const response = await anonymous.post("/api/auth/login", { headers: { origin: BASE_URL, "x-pre-auth": secret, "x-forwarded-for": "6.6.6.6", "x-client-ip": "6.6.6.6", "x-real-ip": "6.6.6.6" }, data: { email: "ghost@example.test", password: "ghost password value" } });
      expect(response.status()).toBe(401);
    }
    expect(testRow("select count(*) from security_events where source = '6.6.6.6'")).toBe("0");
    await anonymous.dispose();
  });
});

test.describe("the application keeps working under session authentication", () => {
  test("every application area answers for a signed-in user and for nobody else", async ({ context, playwright }) => {
    await signInWithPassword(context, FREDRIK);
    const anonymous = await playwright.request.newContext({ baseURL: BASE_URL });
    for (const path of ["/customers", "/items", "/horses", "/transactions", "/invoices", "/organization", "/custom-fields/entity-types", "/me/user"]) {
      expect((await context.request.get(bffUrl(ORG_A.id, path))).status(), path).toBe(200);
      expect((await anonymous.get(bffUrl(ORG_A.id, path))).status(), path).toBe(401);
    }
    await anonymous.dispose();
  });
});
