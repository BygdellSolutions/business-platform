import { randomUUID } from "node:crypto";

import { expect, test, waitForHydration } from "../fixtures";
import type { BrowserContext, Page } from "@playwright/test";

import { E2E_PASSWORD, ensureCredential, signInWithPassword } from "../auth-support";
import { BASE_URL } from "../env";
import { bffUrl, createAccount, createWorld, purgeUsersByEmail, sql, testRow, type Account, type World } from "../support";

/**
 * Invitations under REAL authentication, end to end: an owner or admin invites, the invitee opens the copied link
 * and joins as an existing or a new account. The invitation secret lives in the URL fragment; these specs watch the
 * address, every request URL and the storage for it.
 */

const worlds: World[] = [];
const accounts: Account[] = [];
const created: string[] = [];
test.afterEach(() => {
  for (const world of worlds.splice(0)) world.cleanup();
  for (const account of accounts.splice(0)) account.cleanup();
  purgeUsersByEmail(created.splice(0));
});

const roleOf = (email: string, orgId: string) =>
  testRow(`select ou.role from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(email)} and ou.organization_id = ${sql(orgId)}`);
const invitationRow = (orgId: string, email: string) => testRow(`select coalesce(revoked_at::text,'') || '|' || coalesce(accepted_at::text,'') from organization_invitations where organization_id = ${sql(orgId)} and email = ${sql(email)} order by created_at desc limit 1`);
const newEmail = () => {
  const email = `invitee-${randomUUID().slice(0, 8)}@invitees.test`;
  created.push(email);
  return email;
};

function world(): World {
  const made = createWorld({ label: "Invite" });
  worlds.push(made);
  return made;
}

function account(): Account {
  const made = createAccount({ canCreate: false, label: "existing" });
  accounts.push(made);
  return made;
}

/** Create an invitation as the context's signed-in user through the BFF; returns the link the administrator would copy. */
async function invitationLink(context: BrowserContext, orgId: string, email: string, role: string): Promise<{ link: string; token: string; id: string }> {
  const response = await context.request.post(bffUrl(orgId, "/invitations"), { data: { email, role } });
  expect(response.status(), await response.text()).toBe(201);
  const body = (await response.json()) as { token: string; id: string };
  return { link: `${BASE_URL}/invite#${body.token}`, token: body.token, id: body.id };
}

async function stranger(browser: import("@playwright/test").Browser): Promise<{ context: BrowserContext; page: Page; urls: string[] }> {
  const context = await browser.newContext({ baseURL: BASE_URL });
  const page = waitForHydration(await context.newPage());
  const urls: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  return { context, page, urls };
}

async function openLink(page: Page, link: string) {
  await page.goto(link);
  await expect(page.getByTestId("invite-ready").or(page.getByTestId("invite-invalid"))).toBeVisible();
}

test("an owner creates an invitation in the browser: the link is shown once, in the fragment, and cannot be recovered", async ({ page, context }) => {
  const w = world();
  const invitee = newEmail();
  await signInWithPassword(context, w.email);
  await page.goto(`/o/${w.orgId}/members`);

  await page.getByLabel("Email").fill(invitee);
  await page.getByTestId("invite-role-select").selectOption("admin");
  await page.getByTestId("invite-submit").click();

  const input = page.getByTestId("invitation-link");
  await expect(input).toBeVisible();
  const link = await input.inputValue();
  expect(link).toMatch(new RegExp(`^${BASE_URL}/invite#[A-Za-z0-9_-]{43}$`));
  await expect(page.getByTestId("invitation-link-panel")).toContainText("cannot be retrieved again");
  await expect(page.locator(`[data-testid=invitation-row][data-email="${invitee}"]`)).toContainText("Admin");

  const token = link.split("#")[1];
  await page.reload(); // the link is in memory only
  await expect(page.getByTestId("invitation-link")).toHaveCount(0);
  expect(await page.content()).not.toContain(token);
  expect(await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie]))).not.toContain(token);
  const listed = await context.request.get(bffUrl(w.orgId, "/invitations"));
  expect(await listed.text()).not.toContain(token); // never listed
  expect(testRow(`select count(*) from organization_invitations where token_hash = ${sql(token)}`)).toBe("0"); // and only a hash is stored
});

test("an admin is offered only the roles an admin may invite, and the backend refuses owner and admin anyway", async ({ page, context }) => {
  const w = world();
  const admin = w.addMember("admin");
  await signInWithPassword(context, admin);
  await page.goto(`/o/${w.orgId}/members`);

  await expect(page.getByTestId("invite-role-select").locator("option")).toHaveText(["Accountant", "Employee", "Viewer"]);
  for (const role of ["owner", "admin"]) {
    const response = await context.request.post(bffUrl(w.orgId, "/invitations"), { data: { email: newEmail(), role } });
    expect(response.status()).toBe(403);
  }
  expect(testRow(`select count(*) from organization_invitations where organization_id = ${sql(w.orgId)}`)).toBe("0");
});

test("ordinary roles have no invitation administration at all", async ({ page, context }) => {
  const w = world();
  const viewer = w.addMember("viewer");
  await signInWithPassword(context, viewer);
  await page.goto(`/o/${w.orgId}/members`);
  await expect(page.getByTestId("members-not-allowed")).toBeVisible();
  await expect(page.getByTestId("invite-form")).toHaveCount(0);
  expect((await context.request.get(bffUrl(w.orgId, "/invitations"))).status()).toBe(403);
});

test("opening the link removes the secret from the address at once; a reload cannot recover it; no request URL carries it", async ({ browser, context }) => {
  const w = world();
  await signInWithPassword(context, w.email);
  const invitee = newEmail();
  const { link, token } = await invitationLink(context, w.orgId, invitee, "viewer");
  const { context: outsider, page, urls } = await stranger(browser);

  await openLink(page, link);

  await expect(page.getByTestId("invite-organization")).toHaveText(w.name);
  expect(page.url()).toBe(`${BASE_URL}/invite`); // no fragment
  expect(await page.evaluate(() => location.hash)).toBe("");
  expect(await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie]))).not.toContain(token);
  expect(urls.filter((url) => url.includes(token))).toEqual([]);
  expect(await page.content()).not.toContain(token);

  await page.reload();
  await expect(page.getByTestId("invite-invalid")).toBeVisible(); // the link must be opened again
  await outsider.close();
});

test("a new person creates an account from the link, with a weak-password retry, and lands in the organization with the invited role", async ({ browser, context }) => {
  const w = world();
  await signInWithPassword(context, w.email);
  const invitee = newEmail();
  const { link } = await invitationLink(context, w.orgId, invitee, "accountant");
  const { context: outsider, page, urls } = await stranger(browser);
  const logs: string[] = [];
  page.on("console", (message) => logs.push(message.text()));
  page.on("pageerror", (error) => logs.push(error.message));
  await openLink(page, link);

  await expect(page.getByTestId("invite-email")).toHaveText(invitee);
  await expect(page.getByTestId("invite-role")).toHaveText("accountant");
  await expect(page.getByLabel(/^email$/i)).toHaveCount(0); // the email cannot be changed
  await page.getByLabel("Your name").fill("Nina Newcomer");

  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByLabel("Repeat the password").fill("short");
  await page.getByTestId("invite-create").click();
  await expect(page.getByTestId("invite-error")).toContainText("at least");
  expect(testRow(`select count(*) from users where email = ${sql(invitee)}`)).toBe("0"); // a weak password consumed nothing
  expect(invitationRow(w.orgId, invitee)).toBe("|");

  await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD);
  await page.getByLabel("Repeat the password").fill(E2E_PASSWORD);
  await page.getByTestId("invite-create").click();

  await expect(page).toHaveURL(new RegExp(`/o/${w.orgId}$`));
  await expect(page.getByTestId("org-name")).toHaveText(w.name);
  await expect(page.getByTestId("org-role")).toHaveText("accountant");
  await expect(page.getByTestId("user-email")).toHaveText(invitee);
  expect(roleOf(invitee, w.orgId)).toBe("accountant");
  expect(invitationRow(w.orgId, invitee)).toMatch(/\|\d{4}-/); // accepted
  expect((await outsider.cookies()).map((cookie) => cookie.name).sort()).toEqual(["bp_csrf", "bp_session"]); // no organization cookie
  expect(urls.some((url) => url.includes("#"))).toBe(false);
  expect(logs.filter((line) => line.includes(link.split("#")[1]) || line.includes(E2E_PASSWORD))).toEqual([]); // nothing secret in the browser log

  await page.goto("/"); // the ordinary memberships read shows it
  await expect(page).toHaveURL(new RegExp(`/o/${w.orgId}$`)); // one organization: entered directly
  await outsider.close();
});

test("an existing account that is signed out signs in on the invite page and joins, without the token leaving the page", async ({ browser, context }) => {
  const w = world();
  const existing = account();
  await ensureCredential(existing.email);
  await signInWithPassword(context, w.email);
  const { link, token } = await invitationLink(context, w.orgId, existing.email, "employee");
  const { context: outsider, page, urls } = await stranger(browser);
  await openLink(page, link);

  await expect(page.getByTestId("invite-sign-in-form")).toBeVisible();
  await page.getByLabel("Password").fill(E2E_PASSWORD);
  await page.getByTestId("invite-sign-in").click();

  await expect(page).toHaveURL(new RegExp(`/o/${w.orgId}$`));
  await expect(page.getByTestId("org-role")).toHaveText("employee");
  expect(roleOf(existing.email, w.orgId)).toBe("employee");
  expect(urls.filter((url) => url.includes(token) || url.includes("next="))).toEqual([]);
  await outsider.close();
});

test("an existing account that is already signed in as the invited email just joins", async ({ browser, context }) => {
  const w = world();
  const existing = account();
  await signInWithPassword(context, w.email);
  const { link } = await invitationLink(context, w.orgId, existing.email, "viewer");
  const { context: member, page } = await stranger(browser);
  await signInWithPassword(member, existing.email);
  await openLink(page, link);

  await page.getByTestId("invite-join").click();

  await expect(page).toHaveURL(new RegExp(`/o/${w.orgId}$`));
  await expect(page.getByTestId("org-role")).toHaveText("viewer");
  await member.close();
});

test("the WRONG account is refused without consuming the invitation; signing out on the page and continuing as the invited account works", async ({ browser, context }) => {
  const w = world();
  const invited = account();
  const other = account();
  await ensureCredential(invited.email);
  await signInWithPassword(context, w.email);
  const { link } = await invitationLink(context, w.orgId, invited.email, "viewer");
  const { context: wrong, page } = await stranger(browser);
  await signInWithPassword(wrong, other.email);
  await openLink(page, link);

  await expect(page.getByTestId("invite-wrong-account")).toContainText(other.email);
  await expect(page.getByTestId("invite-join")).toHaveCount(0);
  expect(roleOf(other.email, w.orgId)).toBe("");
  expect(invitationRow(w.orgId, invited.email)).toBe("|"); // untouched

  await page.getByTestId("invite-sign-out").click();
  await expect(page.getByTestId("invite-sign-in-form")).toBeVisible(); // the same page, the token still in memory
  await page.getByLabel("Password").fill(E2E_PASSWORD);
  await page.getByTestId("invite-sign-in").click();

  await expect(page).toHaveURL(new RegExp(`/o/${w.orgId}$`));
  expect(roleOf(invited.email, w.orgId)).toBe("viewer");
  expect(roleOf(other.email, w.orgId)).toBe("");
  await wrong.close();
});

test("an invitation never changes the role of someone who is already a member", async ({ browser, context }) => {
  const w = world();
  const existing = account();
  await signInWithPassword(context, w.email);
  const { link } = await invitationLink(context, w.orgId, existing.email, "admin");
  testRow(`insert into organization_users (organization_id, user_id, role) values (${sql(w.orgId)}, ${sql(existing.id)}, 'viewer')`); // joined another way meanwhile
  const { context: member, page } = await stranger(browser);
  await signInWithPassword(member, existing.email);
  await openLink(page, link);

  await page.getByTestId("invite-join").click();

  await expect(page).toHaveURL(new RegExp(`/o/${w.orgId}$`));
  await expect(page.getByTestId("org-role")).toHaveText("viewer");
  expect(roleOf(existing.email, w.orgId)).toBe("viewer");
  expect(invitationRow(w.orgId, existing.email)).toMatch(/\|\d{4}-/); // settled
  await member.close();
});

test("a revoked, an expired and a made-up link all show the same generic state", async ({ browser, context }) => {
  const w = world();
  await signInWithPassword(context, w.email);
  const revoked = await invitationLink(context, w.orgId, newEmail(), "viewer");
  const expired = await invitationLink(context, w.orgId, newEmail(), "viewer");
  expect((await context.request.delete(bffUrl(w.orgId, `/invitations/${revoked.id}`))).status()).toBe(204);
  testRow(`update organization_invitations set expires_at = now() - interval '1 minute' where id = ${sql(expired.id)}`);

  const texts: string[] = [];
  for (const link of [revoked.link, expired.link, `${BASE_URL}/invite#${"A".repeat(43)}`, `${BASE_URL}/invite#nonsense`]) {
    const { context: outsider, page: invitee } = await stranger(browser);
    await openLink(invitee, link);
    texts.push((await invitee.getByTestId("invite-invalid").textContent()) ?? "");
    await outsider.close();
  }
  expect(new Set(texts).size).toBe(1);
});

test("regenerating gives a new link and kills the old one", async ({ browser, context, page }) => {
  const w = world();
  await signInWithPassword(context, w.email);
  const invitee = newEmail();
  const first = await invitationLink(context, w.orgId, invitee, "viewer");
  await page.goto(`/o/${w.orgId}/members`);

  await page.locator(`[data-testid=invitation-row][data-email="${invitee}"]`).getByTestId("invitation-regenerate").click();
  const fresh = await page.getByTestId("invitation-link").inputValue();

  expect(fresh).not.toBe(first.link);
  const old = await stranger(browser);
  await openLink(old.page, first.link);
  await expect(old.page.getByTestId("invite-invalid")).toBeVisible();
  await old.context.close();
  const next = await stranger(browser);
  await openLink(next.page, fresh);
  await expect(next.page.getByTestId("invite-ready")).toBeVisible();
  await next.context.close();
});

test("revoking removes the invitation, never a membership; an admin cannot revoke an admin invitation", async ({ context }) => {
  const w = world();
  const admin = w.addMember("admin");
  await signInWithPassword(context, w.email);
  const viewerEmail = newEmail();
  await invitationLink(context, w.orgId, viewerEmail, "viewer");
  const adminEmail = newEmail();
  await invitationLink(context, w.orgId, adminEmail, "admin");

  const adminContext = await context.browser()!.newContext({ baseURL: BASE_URL });
  await signInWithPassword(adminContext, admin);
  const adminPage = waitForHydration(await adminContext.newPage());
  await adminPage.goto(`/o/${w.orgId}/members`);
  await expect(adminPage.locator(`[data-testid=invitation-row][data-email="${adminEmail}"]`).getByTestId("invitation-revoke")).toHaveCount(0);
  await adminPage.locator(`[data-testid=invitation-row][data-email="${viewerEmail}"]`).getByTestId("invitation-revoke").click();
  await adminPage.getByRole("button", { name: "Revoke invitation" }).click();
  await expect(adminPage.locator(`[data-testid=invitation-row][data-email="${viewerEmail}"]`)).toHaveCount(0);
  expect(invitationRow(w.orgId, viewerEmail)).toMatch(/^\d{4}-/); // revoked, not deleted
  expect(testRow(`select count(*) from organization_users where organization_id = ${sql(w.orgId)}`)).toBe("2"); // nobody was removed
  await adminContext.close();
});

test("the same link in two tabs: only one acceptance creates the membership", async ({ browser, context }) => {
  const w = world();
  const existing = account();
  await ensureCredential(existing.email);
  await signInWithPassword(context, w.email);
  const { link } = await invitationLink(context, w.orgId, existing.email, "employee");
  const first = await stranger(browser);
  await signInWithPassword(first.context, existing.email);
  const second = await stranger(browser);
  await signInWithPassword(second.context, existing.email);
  await openLink(first.page, link);
  await openLink(second.page, link);

  await Promise.all([first.page.getByTestId("invite-join").click(), second.page.getByTestId("invite-join").click()]);

  await expect(first.page).toHaveURL(new RegExp(`/o/${w.orgId}$`));
  await expect(second.page).toHaveURL(new RegExp(`/o/${w.orgId}$`)); // the same person: the second is the idempotent retry
  expect(testRow(`select count(*) from organization_users where organization_id = ${sql(w.orgId)} and user_id = ${sql(existing.id)}`)).toBe("1");
  expect(testRow(`select count(*) from security_events where organization_id = ${sql(w.orgId)} and event_type = 'invitation_accepted'`)).toBe("1");
  await first.context.close();
  await second.context.close();
});

test("a stranger cannot use the link as another account, and the token is not a tenant selector", async ({ browser, context }) => {
  const w = world();
  const other = world();
  const existing = account();
  await signInWithPassword(context, w.email);
  const { token } = await invitationLink(context, w.orgId, existing.email, "viewer");
  const outsider = await browser.newContext({ baseURL: BASE_URL });
  await signInWithPassword(outsider, other.email); // a member of a different organization

  const attempt = await outsider.request.post("/api/invite/accept", { data: { token } });
  expect(attempt.status()).toBe(403); // wrong account
  expect(roleOf(other.email, w.orgId)).toBe("");
  const forged = await outsider.request.post("/api/invite/accept", { data: { token, organization_id: other.orgId, role: "owner" } });
  expect([forged.status()]).toEqual([403]); // body fields are not forwarded, so it is the same refusal
  expect(testRow(`select count(*) from organization_users where user_id = ${sql(existing.id)}`)).toBe("0");
  await outsider.close();
});
