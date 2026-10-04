import { expect, test } from "./fixtures";

import { BASE_URL } from "./env";
import {
  ORG_A,
  bffUrl,
  createAccount,
  createCustomer,
  createWorld,
  membersOf,
  onboardingCounts,
  signIn,
  sql,
  testRow,
  unique,
  type Account,
} from "./support";

/**
 * Organization onboarding, end to end: an authenticated user creates an organization and becomes its owner, then
 * works in it through the ordinary tenant path. Runs in the dev run AND (unchanged) in the session run, where the
 * sign-in is a real password login; the session-only attacks are in session/onboarding.spec.ts.
 */

const UUID_URL = /\/o\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const accounts: Account[] = [];
const worlds: ReturnType<typeof createWorld>[] = [];
test.afterEach(() => {
  for (const account of accounts.splice(0)) account.cleanup();
  for (const world of worlds.splice(0)) world.cleanup();
});

function account(options: Parameters<typeof createAccount>[0]): Account {
  const made = createAccount(options);
  accounts.push(made);
  return made;
}

async function fillAndSubmit(page: import("@playwright/test").Page, name: string, currency: string) {
  await page.getByLabel(/Organization name/).fill(name);
  if (currency) await page.getByLabel(/Currency/).fill(currency);
  await page.getByTestId("submit").click();
}

test("a newcomer with the right and no organization creates one, becomes its owner and lands in it", async ({ page, context }) => {
  const me = account({ canCreate: true });
  const name = unique("Fresh Org");
  await signIn(context, me.email);

  await page.goto("/");
  await expect(page.getByTestId("no-organizations")).toBeVisible();
  await page.getByTestId("create-organization-link").click();
  await expect(page.getByRole("heading", { name: "Create an organization" })).toBeVisible();
  await expect(page.getByLabel(/Currency/)).toHaveValue(""); // nothing assumed

  await fillAndSubmit(page, name, "eur");

  await expect(page).toHaveURL(UUID_URL);
  await expect(page.getByTestId("org-name")).toHaveText(name);
  await expect(page.getByTestId("org-role")).toHaveText("owner");
  await expect(page.getByTestId("user-email")).toHaveText(me.email);

  const [orgId] = me.createdOrganizations();
  expect(page.url()).toContain(`/o/${orgId}`);
  expect(membersOf(orgId)).toEqual([`${me.email}:owner`]); // exactly one owner: the creator
  expect(testRow(`select default_currency from organizations where id = ${sql(orgId)}`)).toBe("EUR");

  // The existing app works in it, through the ordinary tenant path (the BFF reads the URL, FastAPI the membership).
  const customer = await createCustomer(context, orgId, "Anna Andersson");
  expect(customer.name).toBe("Anna Andersson");
  await page.goto(`/o/${orgId}/customers`);
  await expect(page.getByText("Anna Andersson")).toBeVisible();
  const mine = await context.request.get(bffUrl(orgId, "/me"));
  expect(mine.status()).toBe(200);
  expect(((await mine.json()) as { role: string; user: { email: string } }).role).toBe("owner"); // FastAPI resolved the new membership
});

test("the new organization is an ordinary membership: it appears in the switcher with the owner role, and in the home list", async ({ page, context }) => {
  const world = createWorld({ label: "Existing" });
  worlds.push(world);
  const me = account({ canCreate: true, memberships: [{ orgId: world.orgId, role: "employee" }] });
  const name = unique("Second Org");
  await signIn(context, me.email);

  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("org-role")).toHaveText("employee");
  await page.getByTestId("create-organization-link").click(); // the explicit path from the organization switcher
  await fillAndSubmit(page, name, "SEK");
  await expect(page).toHaveURL(UUID_URL);

  const switcher = page.getByTestId("org-switcher");
  await expect(switcher.getByText(name)).toHaveAttribute("aria-current", "true");
  await expect(switcher.getByRole("link", { name: world.name })).toBeVisible();

  await page.goto("/");
  await expect(page.getByTestId("organization-list").getByText(`(owner)`)).toHaveCount(1);
  await expect(page.getByTestId("organization-list").getByText("(employee)")).toHaveCount(1);
  await expect(page.getByTestId("create-organization-link")).toBeVisible(); // explicit path from the selection page too
});

test("the URL stays the only tenant selector: two tabs stay in different organizations, and nothing is remembered", async ({ context, page }) => {
  const world = createWorld({ label: "Tab" });
  worlds.push(world);
  const me = account({ canCreate: true, memberships: [{ orgId: world.orgId, role: "owner" }] });
  await signIn(context, me.email);
  const first = page;
  await first.goto(`/o/${world.orgId}`);
  await expect(first.getByTestId("org-name")).toHaveText(world.name);
  const cookiesBefore = (await context.cookies()).map((cookie) => cookie.name).sort();

  const second = await context.newPage();
  await second.goto("/organizations/new");
  const name = unique("Tab Org");
  await fillAndSubmit(second, name, "NOK");
  await expect(second).toHaveURL(UUID_URL);
  await expect(second.getByTestId("org-name")).toHaveText(name);

  await first.reload();
  await expect(first.getByTestId("org-name")).toHaveText(world.name); // not pulled into the new organization
  expect((await context.cookies()).map((cookie) => cookie.name).sort()).toEqual(cookiesBefore); // no active-organization cookie
  expect(await second.evaluate(() => window.localStorage.length + window.sessionStorage.length)).toBe(0);
});

test("a user without the right is shown no creation control and the backend refuses a forged attempt", async ({ page, context }) => {
  const world = createWorld({ label: "Plain" });
  worlds.push(world);
  const me = account({ canCreate: false, memberships: [{ orgId: world.orgId, role: "owner" }] }); // owning an organization is not the right
  await signIn(context, me.email);
  const before = onboardingCounts();

  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("create-organization-link")).toHaveCount(0);
  await page.goto("/organizations/new");
  await expect(page.getByTestId("creation-not-allowed")).toBeVisible();
  await expect(page.getByTestId("create-organization-form")).toHaveCount(0);

  const forged = await context.request.post("/api/organizations", { data: { name: unique("Sneaky"), default_currency: "EUR" } });
  expect(forged.status()).toBe(403);
  expect(((await forged.json()) as { detail: { code: string } }).detail.code).toBe("organization_creation_not_allowed");
  expect(onboardingCounts()).toBe(before);
  expect(me.createdOrganizations()).toEqual([]);
});

test("a newcomer without the right and without organizations is told, and offered nothing to click", async ({ page, context }) => {
  const me = account({ canCreate: false });
  await signIn(context, me.email);

  await page.goto("/");
  await expect(page.getByTestId("no-organizations")).toHaveText("You do not belong to any organization yet.");
  await expect(page.getByTestId("create-organization-link")).toHaveCount(0);
});

test("the currency must be chosen: nothing is sent without it, and the form never assumes one", async ({ page, context }) => {
  const me = account({ canCreate: true });
  await signIn(context, me.email);
  const before = onboardingCounts();
  let posts = 0;
  await page.route("**/api/organizations", (route) => {
    if (route.request().method() === "POST") posts += 1;
    return route.continue();
  });

  await page.goto("/organizations/new");
  await fillAndSubmit(page, unique("No Currency"), "");

  await expect(page.getByText("Choose the currency the organization works in.")).toBeVisible();
  expect(posts).toBe(0);
  expect(onboardingCounts()).toBe(before);

  await page.getByLabel(/Currency/).fill("EURO"); // the backend judges the shape
  await page.getByTestId("submit").click();
  await expect(page.locator("[aria-invalid=true]")).toBeVisible();
  expect(onboardingCounts()).toBe(before);
});

test("a lost response: the retry returns the organization that was already created, not a second one", async ({ page, context }) => {
  const me = account({ canCreate: true });
  const name = unique("Lost Answer");
  await signIn(context, me.email);
  await page.goto("/organizations/new");

  let dropped = false;
  await page.route("**/api/organizations", async (route) => {
    if (route.request().method() === "POST" && !dropped) {
      dropped = true;
      await route.fetch(); // the server really creates it...
      await route.abort("failed"); // ...but the browser never sees the answer
      return;
    }
    await route.continue();
  });

  await fillAndSubmit(page, name, "EUR");
  await expect(page.getByTestId("creation-unknown")).toBeVisible();
  expect(me.createdOrganizations()).toHaveLength(1); // it did commit

  await page.getByTestId("submit").click(); // same details, same key
  await expect(page).toHaveURL(UUID_URL);
  await expect(page.getByTestId("org-name")).toHaveText(name);
  expect(me.createdOrganizations()).toHaveLength(1);
  expect(testRow(`select count(*) from organizations where name = ${sql(name)}`)).toBe("1");
  expect(testRow(`select count(*) from organization_creation_requests where user_id = ${sql(me.id)}`)).toBe("1");
});

test("forged bodies cannot name an owner, a role or an organization; the owner is always the signed-in user", async ({ context }) => {
  const me = account({ canCreate: true });
  const other = account({ canCreate: true, label: "other" });
  await signIn(context, me.email);
  const before = onboardingCounts();
  const valid = { name: unique("Forged"), default_currency: "EUR" };

  for (const extra of [{ owner_email: other.email }, { owner_user_id: other.id }, { role: "owner" }, { organization_id: ORG_A.id }, { id: ORG_A.id }, { can_create_organizations: true }]) {
    const response = await context.request.post("/api/organizations", { data: { ...valid, ...extra } });
    expect(response.status(), JSON.stringify(extra)).toBe(422);
  }
  expect(onboardingCounts()).toBe(before);

  const response = await context.request.post("/api/organizations", {
    data: valid,
    headers: { "x-organization-id": ORG_A.id, "x-role": "owner", "x-owner-email": other.email, "x-dev-user-email": other.email },
  });
  expect(response.status(), await response.text()).toBe(201);
  const created = (await response.json()) as { id: string };
  expect(created.id).not.toBe(ORG_A.id);
  expect(membersOf(created.id)).toEqual([`${me.email}:owner`]);
  expect(testRow(`select count(*) from organization_users where user_id = ${sql(other.id)}`)).toBe("0");
});

test("without a session nothing is created", async ({ playwright }) => {
  const anonymous = await playwright.request.newContext({ baseURL: BASE_URL });
  const before = onboardingCounts();
  const response = await anonymous.post("/api/organizations", { headers: { origin: BASE_URL, "x-csrf-token": "C".repeat(43) }, data: { name: "Anonymous", default_currency: "EUR" } });
  expect(response.status()).toBe(401);
  expect(onboardingCounts()).toBe(before);
  await anonymous.dispose();
});

test("an organization without any owner (created before onboarding existed) is left exactly as it is", async ({ page, context }) => {
  const legacy = unique("Legacy ownerless");
  const legacyId = testRow(`insert into organizations (name, default_currency) values (${sql(legacy)}, 'SEK') returning id`).split("\n")[0];
  const me = account({ canCreate: true });
  try {
    const before = testRow(`select name || '|' || coalesce(default_currency, '') || '|' || updated_at::text from organizations where id = ${sql(legacyId)}`);
    await signIn(context, me.email);
    await page.goto("/organizations/new");
    await fillAndSubmit(page, unique("Brand New"), "EUR");
    await expect(page).toHaveURL(UUID_URL);

    expect(testRow(`select name || '|' || coalesce(default_currency, '') || '|' || updated_at::text from organizations where id = ${sql(legacyId)}`)).toBe(before);
    expect(membersOf(legacyId)).toEqual([]); // still ownerless: nothing repaired, nothing assigned
    expect(me.createdOrganizations()).toHaveLength(1);
  } finally {
    testRow(`delete from organizations where id = ${sql(legacyId)}`);
  }
});
