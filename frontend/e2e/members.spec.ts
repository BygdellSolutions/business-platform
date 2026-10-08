import { expect, test } from "./fixtures";

import { E2E_PASSWORD } from "./auth-support";
import { BASE_URL } from "./env";
import { bffUrl, createWorld, signIn, sql, testRow, type World } from "./support";

/**
 * Membership administration, end to end (dev run AND session run, unchanged): authority by role, the role and
 * removal flows, leaving, stale screens settling into the truth, tenant isolation between look-alike organizations,
 * and the last owner. The races on committed data are proved in the backend suite; here the browser meets them.
 */

const worlds: World[] = [];
test.afterEach(() => {
  for (const world of worlds.splice(0)) world.cleanup();
});

function world(): World {
  const made = createWorld({ label: "Members" });
  worlds.push(made);
  return made;
}

const roleOf = (email: string, orgId: string) =>
  testRow(`select ou.role from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(email)} and ou.organization_id = ${sql(orgId)}`);
const memberCount = (orgId: string) => Number(testRow(`select count(*) from organization_users where organization_id = ${sql(orgId)}`));
const rowOf = (page: import("@playwright/test").Page, email: string) => page.locator(`[data-testid=member-row][data-email="${email}"]`);

async function open(page: import("@playwright/test").Page, context: import("@playwright/test").BrowserContext, email: string, orgId: string) {
  await signIn(context, email);
  await page.goto(`/o/${orgId}/members`);
}

test("an owner sees everyone with their roles, and Members is in the navigation", async ({ page, context }) => {
  const w = world();
  const admin = w.addMember("admin");
  const viewer = w.addMember("viewer");
  await open(page, context, w.email, w.orgId);

  await expect(page.getByRole("heading", { name: "Members" })).toBeVisible();
  await expect(page.getByTestId("member-row")).toHaveCount(3);
  await expect(rowOf(page, admin)).toContainText("Admin");
  await expect(rowOf(page, viewer)).toContainText("Viewer");
  await expect(rowOf(page, w.email)).toContainText("(you)");
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Members" })).toBeVisible();
  await expect(rowOf(page, w.email).getByTestId("member-remove")).toHaveCount(0); // leaving is separate from removal
});

test("an admin is offered controls only for accountants, employees and viewers", async ({ page, context }) => {
  const w = world();
  const admin = w.addMember("admin");
  const other = w.addMember("admin");
  const employee = w.addMember("employee");
  await open(page, context, admin, w.orgId);

  for (const email of [w.email, other, admin]) {
    await expect(rowOf(page, email).getByTestId("member-role")).toHaveCount(0);
    await expect(rowOf(page, email).getByTestId("member-remove")).toHaveCount(0);
  }
  await expect(rowOf(page, employee).getByTestId("member-role")).toBeVisible();
  await expect(rowOf(page, employee).getByTestId("member-remove")).toBeVisible();
});

for (const role of ["accountant", "employee", "viewer"] as const) {
  test(`${role}: no Members link, no screen, and the backend refuses the list and every change`, async ({ page, context }) => {
    const w = world();
    const me = w.addMember(role);
    const victim = w.addMember("viewer");
    await signIn(context, me);

    await page.goto(`/o/${w.orgId}`);
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Members" })).toHaveCount(0);
    await page.goto(`/o/${w.orgId}/members`);
    await expect(page.getByTestId("members-not-allowed")).toBeVisible();
    await expect(page.getByTestId("member-row")).toHaveCount(0);

    const list = await context.request.get(bffUrl(w.orgId, "/members"));
    expect(list.status()).toBe(403);
    const victimId = testRow(`select ou.id from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(victim)}`);
    expect((await context.request.patch(bffUrl(w.orgId, `/members/${victimId}`), { data: { role: "owner" } })).status()).toBe(403);
    expect((await context.request.delete(bffUrl(w.orgId, `/members/${victimId}`))).status()).toBe(403);
    expect(roleOf(victim, w.orgId)).toBe("viewer");
  });
}

test("an owner changes a role in the browser and the list shows the server's answer", async ({ page, context }) => {
  const w = world();
  const employee = w.addMember("employee");
  await open(page, context, w.email, w.orgId);

  await rowOf(page, employee).getByTestId("member-role").selectOption("accountant");

  await expect(page.getByTestId("members-message")).toContainText("is now accountant");
  await expect(rowOf(page, employee).getByTestId("member-role")).toHaveValue("accountant");
  expect(roleOf(employee, w.orgId)).toBe("accountant");
  expect(testRow(`select count(*) from security_events where organization_id = ${sql(w.orgId)} and event_type = 'member_role_changed'`)).toBe("1");
});

test("removing a member removes only the membership: the person stays signed in and loses just this organization", async ({ browser, page, context }) => {
  const w = world();
  const victim = w.addMember("employee");
  const other = createWorld({ label: "Other tenant" });
  worlds.push(other);
  const victimId = testRow(`select id from users where email = ${sql(victim)}`);
  testRow(`insert into organization_users (organization_id, user_id, role) values (${sql(other.orgId)}, ${sql(victimId)}, 'viewer')`);

  await open(page, context, w.email, w.orgId);

  await rowOf(page, victim).getByTestId("member-remove").click();
  await page.getByRole("button", { name: "Remove member" }).click();
  await expect(page.getByTestId("members-message")).toContainText("was removed");
  await expect(rowOf(page, victim)).toHaveCount(0);

  expect(roleOf(victim, w.orgId)).toBe("");
  expect(testRow(`select count(*) from users where id = ${sql(victimId)}`)).toBe("1"); // the user survives
  expect(roleOf(victim, other.orgId)).toBe("viewer"); // their other membership survives

  // The removed person: still authenticated (the session is global), but this organization is gone for them.
  const person = await browser.newContext({ baseURL: BASE_URL });
  await signIn(person, victim);
  expect((await person.request.get(bffUrl(w.orgId, "/customers"))).status()).toBe(404);
  expect((await person.request.get(bffUrl(other.orgId, "/customers"))).status()).toBe(200);
  await person.close();
});

test("leaving returns to the organization selection; the person stays signed in with an empty state", async ({ page, context }) => {
  const w = world();
  const me = w.addMember("employee");
  await signIn(context, me);
  await page.goto(`/o/${w.orgId}/settings`);

  // Recent authentication: checked in the session run, ignored by the development sign-in.
  await page.getByTestId("danger-zone").getByLabel("Your password").fill(E2E_PASSWORD);
  await page.getByTestId("danger-zone").getByTestId("leave-organization").click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("no-organizations")).toBeVisible();
  await expect(page.getByTestId("user-email")).toHaveText(me); // still signed in
  await expect(page.getByTestId("create-organization-link")).toBeVisible(); // Owned 0 / 1: a default account may own one
  expect(roleOf(me, w.orgId)).toBe("");
  expect(testRow(`select count(*) from security_events where organization_id = ${sql(w.orgId)} and event_type = 'member_left'`)).toBe("1");
});

test("the last owner cannot leave or step down: the server refuses and nothing changes", async ({ page, context }) => {
  const w = world();
  await signIn(context, w.email);
  await page.goto(`/o/${w.orgId}/settings`);

  // The sole owner is told to transfer or delete, and is offered no leave at all...
  await expect(page.getByTestId("sole-owner")).toContainText("You are the only owner of this organization.");
  await expect(page.getByTestId("leave-organization")).toHaveCount(0);
  // ...and the server refuses a forged leave too.
  const forged = await context.request.post(bffUrl(w.orgId, "/members/leave"), { data: {} });
  expect(forged.status()).toBe(409);

  const myId = testRow(`select ou.id from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(w.email)}`);
  const stepDown = await context.request.patch(bffUrl(w.orgId, `/members/${myId}`), { data: { role: "admin" } });
  expect(stepDown.status()).toBe(409);
  expect(((await stepDown.json()) as { detail: { code: string } }).detail.code).toBe("last_owner");
  const removal = await context.request.delete(bffUrl(w.orgId, `/members/${myId}`));
  expect(removal.status()).toBe(403); // removing yourself is leave, which is refused as above
  expect(roleOf(w.email, w.orgId)).toBe("owner");
});

test.describe("a stale screen settles into the truth", () => {
  test("the target was promoted to admin meanwhile: the admin's change is refused, nothing is claimed, the controls disappear", async ({ page, context }) => {
    const w = world();
    const admin = w.addMember("admin");
    const employee = w.addMember("employee");
    await open(page, context, admin, w.orgId);
    await expect(rowOf(page, employee).getByTestId("member-role")).toBeVisible();

    testRow(`update organization_users set role = 'admin' where user_id = (select id from users where email = ${sql(employee)}) and organization_id = ${sql(w.orgId)}`); // someone else did this

    await rowOf(page, employee).getByTestId("member-role").selectOption("viewer");

    const message = page.getByTestId("members-message");
    await expect(message).toContainText("not allowed to do that");
    await expect(message).not.toContainText("is now");
    await expect(rowOf(page, employee).getByTestId("member-role")).toHaveCount(0); // refreshed: an admin is no longer theirs to manage
    expect(roleOf(employee, w.orgId)).toBe("admin");
  });

  test("the target was removed meanwhile: the answer is not-found and the row disappears", async ({ page, context }) => {
    const w = world();
    const viewer = w.addMember("viewer");
    await open(page, context, w.email, w.orgId);
    await expect(rowOf(page, viewer)).toBeVisible();

    testRow(`delete from organization_users where user_id = (select id from users where email = ${sql(viewer)}) and organization_id = ${sql(w.orgId)}`);

    await rowOf(page, viewer).getByTestId("member-role").selectOption("employee");

    await expect(page.getByTestId("members-message")).toContainText("no longer exists");
    await expect(rowOf(page, viewer)).toHaveCount(0);
  });

  test("the actor was demoted meanwhile: the action is refused and the screen turns into the not-allowed notice", async ({ page, context }) => {
    const w = world();
    const admin = w.addMember("admin");
    const viewer = w.addMember("viewer");
    await open(page, context, admin, w.orgId);
    await expect(rowOf(page, viewer).getByTestId("member-remove")).toBeVisible();

    testRow(`update organization_users set role = 'employee' where user_id = (select id from users where email = ${sql(admin)}) and organization_id = ${sql(w.orgId)}`);

    await rowOf(page, viewer).getByTestId("member-remove").click();
    await page.getByRole("button", { name: "Remove member" }).click();

    await expect(page.getByTestId("members-not-allowed")).toBeVisible(); // refreshed from the server: no longer an administrator
    expect(roleOf(viewer, w.orgId)).toBe("viewer");
  });
});

test("look-alike organizations: a membership id of another organization is just not found, and nothing there changes", async ({ page, context }) => {
  const a = world();
  const b = world();
  const bEmployee = b.addMember("employee");
  a.addMember("employee");
  await open(page, context, a.email, a.orgId);
  const foreignId = testRow(`select ou.id from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(bEmployee)}`);

  const patched = await context.request.patch(bffUrl(a.orgId, `/members/${foreignId}`), { data: { role: "viewer" } });
  const removed = await context.request.delete(bffUrl(a.orgId, `/members/${foreignId}`));
  const random = await context.request.delete(bffUrl(a.orgId, `/members/00000000-0000-4000-8000-00000000dead`));

  expect([patched.status(), removed.status(), random.status()]).toEqual([404, 404, 404]);
  expect(await removed.json()).toEqual(await random.json()); // a foreign id and a random id are indistinguishable
  expect(roleOf(bEmployee, b.orgId)).toBe("employee");
  expect(memberCount(b.orgId)).toBe(2);
  // the owner of A cannot select B at all
  expect((await context.request.get(bffUrl(b.orgId, "/members"))).status()).toBe(404);
});

test("a request cannot carry an actor, an organization or a target user: unknown fields are refused", async ({ context }) => {
  const w = world();
  const employee = w.addMember("employee");
  await signIn(context, w.email);
  const id = testRow(`select ou.id from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(employee)}`);

  for (const extra of [{ organization_id: w.orgId }, { actor_user_id: "x" }, { user_id: "x" }, { email: employee }]) {
    const response = await context.request.patch(bffUrl(w.orgId, `/members/${id}`), { data: { role: "viewer", ...extra } });
    expect(response.status(), JSON.stringify(extra)).toBe(422);
  }
  expect(roleOf(employee, w.orgId)).toBe("employee");
});
