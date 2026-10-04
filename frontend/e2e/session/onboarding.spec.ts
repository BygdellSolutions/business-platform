import { expect, test } from "../fixtures";

import { BASE_URL } from "../env";
import { adminCli, csrfCookie, signInThroughPage, signInWithPassword } from "../auth-support";
import { createAccount, membersOf, onboardingCounts, sql, testRow, unique, type Account } from "../support";

/**
 * Organization onboarding under REAL authentication: the real login, the CSRF layers, and the operator's grant and
 * revoke of the account-level right. (The behaviour itself is in ../onboarding.spec.ts, which also runs in this run.)
 */

const UUID_URL = /\/o\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const accounts: Account[] = [];
test.afterEach(() => {
  for (const made of accounts.splice(0)) made.cleanup();
});
const account = (options: Parameters<typeof createAccount>[0]) => {
  const made = createAccount(options);
  accounts.push(made);
  return made;
};

test("through the real login page: sign in, create, land in the organization; the browser holds only the two protected cookies", async ({ page, context }) => {
  const me = account({ canCreate: true });
  const name = unique("Session Org");

  await signInThroughPage(page, me.email);
  await expect(page.getByTestId("no-organizations")).toBeVisible();
  const cookiesBefore = (await context.cookies()).map((cookie) => cookie.name).sort();
  expect(cookiesBefore).toEqual(["bp_csrf", "bp_session"]);

  await page.getByTestId("create-organization-link").click();
  await page.getByLabel(/Organization name/).fill(name);
  await page.getByLabel(/Currency/).fill("EUR");
  await page.getByTestId("submit").click();

  await expect(page).toHaveURL(UUID_URL);
  await expect(page.getByTestId("org-role")).toHaveText("owner");
  expect((await context.cookies()).map((cookie) => cookie.name).sort()).toEqual(cookiesBefore); // no active-organization cookie

  const [orgId] = me.createdOrganizations();
  expect(membersOf(orgId)).toEqual([`${me.email}:owner`]);
  // The security event: who, which organization; never the form's contents.
  expect(testRow(`select count(*) from security_events where event_type = 'organization_created' and actor_user_id = ${sql(me.id)} and organization_id = ${sql(orgId)} and detail = 'keyed'`)).toBe("1");
  expect(testRow(`select count(*) from security_events where actor_user_id = ${sql(me.id)} and (detail ilike ${sql("%" + name + "%")} or source ilike ${sql("%" + name + "%")})`)).toBe("0");
});

test.describe("CSRF on organization creation", () => {
  const body = () => ({ name: unique("Csrf Org"), default_currency: "EUR" });

  test("BFF layer: a missing or mismatched CSRF header, or a foreign Origin, is refused and nothing is created", async ({ context }) => {
    const me = account({ canCreate: true });
    await signInWithPassword(context, me.email);
    const before = onboardingCounts();

    const missing = await context.request.post("/api/organizations", { headers: { "x-csrf-token": "" }, data: body() });
    const wrong = await context.request.post("/api/organizations", { headers: { "x-csrf-token": "W".repeat(43) }, data: body() });
    const foreign = await context.request.post("/api/organizations", { headers: { origin: "https://evil.example" }, data: body() });

    expect([missing.status(), wrong.status(), foreign.status()]).toEqual([403, 403, 403]);
    expect(onboardingCounts()).toBe(before);
  });

  test("FastAPI layer: a cookie and header the BFF accepts, but that are not this session's token, are still refused", async ({ context }) => {
    const me = account({ canCreate: true });
    await signInWithPassword(context, me.email);
    const forged = "Z".repeat(43);
    await context.addCookies([{ name: "bp_csrf", value: forged, url: BASE_URL, sameSite: "Lax" }]);
    const before = onboardingCounts();

    const response = await context.request.post("/api/organizations", { headers: { "x-csrf-token": forged }, data: body() });

    expect(response.status()).toBe(403);
    expect(((await response.json()) as { detail: { code: string } }).detail.code).toBe("csrf_failed");
    expect(onboardingCounts()).toBe(before);
    expect(await csrfCookie(context)).toBe(forged); // (the test planted it; the real token is not what was used)
  });
});

test("the operator's grant and revoke take effect immediately for a signed-in user, without touching memberships", async ({ page }) => {
  const me = account({ canCreate: false });
  await signInThroughPage(page, me.email);
  await page.goto("/organizations/new");
  await expect(page.getByTestId("creation-not-allowed")).toBeVisible();

  adminCli(["grant-org-creation", "--email", me.email]);
  await page.goto("/organizations/new");
  await expect(page.getByTestId("create-organization-form")).toBeVisible();

  // The page was rendered while allowed; the right is revoked before the form is submitted: the backend decides.
  adminCli(["revoke-org-creation", "--email", me.email]);
  await page.getByLabel(/Organization name/).fill(unique("Too Late"));
  await page.getByLabel(/Currency/).fill("EUR");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("creation-refused")).toBeVisible();
  expect(me.createdOrganizations()).toEqual([]);

  adminCli(["grant-org-creation", "--email", me.email]);
  await page.getByTestId("submit").click();
  await expect(page).toHaveURL(UUID_URL);

  expect(testRow(`select string_agg(detail, ',' order by id) from security_events where event_type = 'capability_changed' and actor_user_id = ${sql(me.id)}`)).toBe("org_creation_granted:cli,org_creation_revoked:cli,org_creation_granted:cli");
  expect(testRow(`select count(*) from organization_users where user_id = ${sql(me.id)}`)).toBe("1"); // only the organization it created
});

test("the development identity configured on the backend is ignored: the session user is the owner", async ({ context }) => {
  const me = account({ canCreate: true });
  await signInWithPassword(context, me.email);

  const response = await context.request.post("/api/organizations", { headers: { "x-dev-user-email": "fredrik@dev.test" }, data: { name: unique("Not Fredrik"), default_currency: "EUR" } });

  expect(response.status(), await response.text()).toBe(201);
  const created = (await response.json()) as { id: string };
  expect(membersOf(created.id)).toEqual([`${me.email}:owner`]);
});
