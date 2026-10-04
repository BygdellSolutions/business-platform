import { randomUUID } from "node:crypto";

import { expect, test } from "../fixtures";

import { E2E_PASSWORD, adminCli, tokenFromCli } from "../auth-support";
import { BACKEND_URL, BASE_URL } from "../env";
import { sql, testRow } from "../support";

/**
 * The first user and recovery: the operator CLI prints a single-use link, the person opens `/setup#<token>`,
 * chooses a password and is signed in. The secret lives in the URL FRAGMENT: it must leave the address bar at
 * once, appear in no request URL, and be used exactly once.
 */

const created: string[] = [];
test.afterEach(() => {
  for (const email of created.splice(0)) {
    testRow(
      `set local session_replication_role = replica; delete from security_events where actor_user_id in (select id from users where email = ${sql(email)}); delete from auth_sessions where user_id in (select id from users where email = ${sql(email)}); delete from user_setup_tokens where user_id in (select id from users where email = ${sql(email)}); delete from user_credentials where user_id in (select id from users where email = ${sql(email)}); delete from users where email = ${sql(email)}`,
    );
  }
});

/** A brand-new user and their setup link, made the way an operator makes them. */
function bootstrap(): { email: string; token: string } {
  const email = `setup-${randomUUID().slice(0, 8)}@example.test`;
  created.push(email);
  const token = tokenFromCli(adminCli(["bootstrap-user", "--email", email, "--name", "Setup Person"]));
  return { email, token };
}

const GOOD_PASSWORD = "a freshly chosen long passphrase";

test("the first user: the link becomes a password and a session, and the secret is gone from the address at once", async ({ page, context }) => {
  const { email, token } = bootstrap();
  const requests: { url: string; body: string | null }[] = [];
  page.on("request", (request) => requests.push({ url: request.url(), body: request.postData() }));

  await page.goto(`/setup#${token}`);
  await expect(page.getByTestId("setup-form")).toHaveAttribute("data-ready", "true");

  // Removed from the address bar and the history entry, before anything else.
  expect(page.url()).toBe(`${BASE_URL}/setup`);
  expect(await page.evaluate(() => location.hash)).toBe("");
  expect(await page.evaluate(() => document.documentElement.outerHTML.includes("#"))).toBe(false);

  await page.getByLabel("New password").fill(GOOD_PASSWORD);
  await page.getByLabel("Repeat the password").fill(GOOD_PASSWORD);
  await page.getByTestId("setup-submit").click();

  await expect(page).toHaveURL(`${BASE_URL}/`);
  await expect(page.getByTestId("user-email")).toHaveText(email);
  await expect(page.getByTestId("no-organizations")).toBeVisible(); // organization onboarding is a later step

  // The secret travelled only in the body of the POST: in no URL, and not in any other request.
  for (const request of requests) expect(request.url).not.toContain(token);
  const setupPosts = requests.filter((request) => request.url.endsWith("/api/auth/setup"));
  expect(setupPosts).toHaveLength(1);
  expect(JSON.parse(setupPosts[0].body!)).toEqual({ token, password: GOOD_PASSWORD });
  expect(requests.filter((request) => request.body?.includes(token))).toHaveLength(1);

  // A real session now, and the pre-auth secret was consumed.
  expect((await context.cookies()).map((c) => c.name).sort()).toEqual(["bp_csrf", "bp_session"]);
  // The password works for an ordinary login afterwards.
  const login = await fetch(`${BACKEND_URL}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password: GOOD_PASSWORD }) });
  expect(login.status).toBe(200);
});

test("the link is gone from history too: going back never shows it", async ({ page }) => {
  const { token } = bootstrap();
  await page.goto("/login");
  await page.goto(`/setup#${token}`);
  await expect(page.getByTestId("setup-form")).toHaveAttribute("data-ready", "true");

  await page.goBack();
  await page.goForward();

  expect(page.url()).not.toContain(token);
  expect(await page.evaluate(() => location.href)).not.toContain(token);
});

test("the link works exactly once", async ({ page, context }) => {
  const { token } = bootstrap();
  await page.goto(`/setup#${token}`);
  await expect(page.getByTestId("setup-form")).toHaveAttribute("data-ready", "true");
  await page.getByLabel("New password").fill(GOOD_PASSWORD);
  await page.getByLabel("Repeat the password").fill(GOOD_PASSWORD);
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(`${BASE_URL}/`);
  await context.clearCookies();

  await page.goto(`/setup#${token}`);
  await expect(page.getByTestId("setup-form")).toHaveAttribute("data-ready", "true");
  await page.getByLabel("New password").fill("another long passphrase here");
  await page.getByLabel("Repeat the password").fill("another long passphrase here");
  await page.getByTestId("setup-submit").click();

  await expect(page.getByTestId("setup-invalid")).toHaveText("This link is invalid or has expired. Ask for a new one.");
  expect((await context.cookies()).map((c) => c.name)).not.toContain("bp_session");
});

test("an expired link, a revoked link, an unknown link and a malformed link all get the same generic message", async ({ page }) => {
  const expired = bootstrap();
  testRow(`update user_setup_tokens set expires_at = now() - interval '1 minute' where user_id = (select id from users where email = ${sql(expired.email)})`);
  const reissued = bootstrap();
  adminCli(["reissue-setup-link", "--email", reissued.email]); // revokes the first link
  const messages: string[] = [];

  for (const token of [expired.token, reissued.token, "U".repeat(43), "short", ""]) {
    await page.goto(token === "" ? "/setup" : `/setup#${token}`);
    if (token.length === 43) {
      await expect(page.getByTestId("setup-form")).toHaveAttribute("data-ready", "true");
      await page.getByLabel("New password").fill(GOOD_PASSWORD);
      await page.getByLabel("Repeat the password").fill(GOOD_PASSWORD);
      await page.getByTestId("setup-submit").click();
    }
    await expect(page.getByTestId("setup-invalid")).toBeVisible();
    messages.push(await page.getByTestId("setup-invalid").innerText());
  }
  expect(new Set(messages).size).toBe(1);
});

test("a mismatching repeat sends nothing, and a weak password shows the backend's wording and keeps the link usable", async ({ page }) => {
  const { token, email } = bootstrap();
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/auth/setup")) posts.push(request.url());
  });
  await page.goto(`/setup#${token}`);
  await expect(page.getByTestId("setup-form")).toHaveAttribute("data-ready", "true");

  await page.getByLabel("New password").fill(GOOD_PASSWORD);
  await page.getByLabel("Repeat the password").fill(GOOD_PASSWORD + "x");
  await page.getByTestId("setup-submit").click();
  await expect(page.getByTestId("setup-error")).toHaveText("The two passwords do not match.");
  expect(posts).toHaveLength(0);

  await page.getByLabel("New password").fill("too short");
  await page.getByLabel("Repeat the password").fill("too short");
  await page.getByTestId("setup-submit").click();
  await expect(page.getByTestId("setup-error")).toHaveText("The password must be at least 12 characters.");
  expect(posts).toHaveLength(1);

  await page.getByLabel("New password").fill(GOOD_PASSWORD);
  await page.getByLabel("Repeat the password").fill(GOOD_PASSWORD);
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(`${BASE_URL}/`);
  await expect(page.getByTestId("user-email")).toHaveText(email);
});

test("recovery: a reissued link replaces the password and ends the old sessions", async ({ page, browser }) => {
  const first = bootstrap();
  const response = await fetch(`${BACKEND_URL}/api/auth/setup`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token: first.token, password: E2E_PASSWORD }) });
  const oldSession = ((await response.json()) as { token: string }).token;
  expect((await fetch(`${BACKEND_URL}/api/me/organizations`, { headers: { authorization: `Bearer ${oldSession}` } })).status).toBe(200);

  const recovery = tokenFromCli(adminCli(["reissue-setup-link", "--email", first.email]));
  await page.goto(`/setup#${recovery}`);
  await expect(page.getByTestId("setup-form")).toHaveAttribute("data-ready", "true");
  await page.getByLabel("New password").fill(GOOD_PASSWORD);
  await page.getByLabel("Repeat the password").fill(GOOD_PASSWORD);
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(`${BASE_URL}/`);

  expect((await fetch(`${BACKEND_URL}/api/me/organizations`, { headers: { authorization: `Bearer ${oldSession}` } })).status).toBe(401);
  const other = await browser.newContext({ baseURL: BASE_URL });
  const old = await fetch(`${BACKEND_URL}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: first.email, password: E2E_PASSWORD }) });
  expect(old.status).toBe(401); // the old password is gone
  await other.close();
});

test("the setup API needs Origin and the pre-auth pair like login does", async ({ context }) => {
  const { token } = bootstrap();
  const attempt = (headers: Record<string, string>) => context.request.post("/api/auth/setup", { headers, data: { token, password: GOOD_PASSWORD } });
  expect((await attempt({})).status()).toBe(403);
  expect((await attempt({ origin: BASE_URL })).status()).toBe(403);
  expect((await attempt({ origin: BASE_URL, "x-pre-auth": "X".repeat(43) })).status()).toBe(403);
  // none of those used the link: it still works
  const pre = ((await (await context.request.get("/api/auth/pre")).json()) as { token: string }).token;
  expect((await attempt({ origin: BASE_URL, "x-pre-auth": pre })).status()).toBe(200);
});
