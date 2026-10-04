import { expect, test } from "../fixtures";

import { BASE_URL } from "../env";
import { signInWithPassword } from "../auth-support";
import { bffUrl, createWorld, sql, testRow, type World } from "../support";

/** Membership administration under REAL authentication: CSRF on every change, and removal leaves global sessions alone. */

const worlds: World[] = [];
test.afterEach(() => {
  for (const world of worlds.splice(0)) world.cleanup();
});

const membershipId = (email: string, orgId: string) => testRow(`select ou.id from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(email)} and ou.organization_id = ${sql(orgId)}`);

test("CSRF, BFF layer: a missing or mismatched token or a foreign Origin refuses every change, leave included", async ({ context }) => {
  const w = createWorld({ label: "Csrf" });
  worlds.push(w);
  const employee = w.addMember("employee");
  const viewer = w.addMember("viewer");
  await signInWithPassword(context, w.email);
  const target = membershipId(viewer, w.orgId);

  const attempts = [
    context.request.patch(bffUrl(w.orgId, `/members/${target}`), { headers: { "x-csrf-token": "" }, data: { role: "employee" } }),
    context.request.delete(bffUrl(w.orgId, `/members/${target}`), { headers: { "x-csrf-token": "W".repeat(43) } }),
    context.request.patch(bffUrl(w.orgId, `/members/${target}`), { headers: { origin: "https://evil.example" }, data: { role: "employee" } }),
    context.request.post(bffUrl(w.orgId, "/members/leave"), { headers: { "x-csrf-token": "" } }),
  ];
  expect((await Promise.all(attempts)).map((r) => r.status())).toEqual([403, 403, 403, 403]);
  expect(testRow(`select count(*) from organization_users where organization_id = ${sql(w.orgId)}`)).toBe("3");
  expect(testRow(`select role from organization_users where id = ${sql(target)}`)).toBe("viewer");
  expect(employee).toBeTruthy();
});

test("CSRF, FastAPI layer: a forged pair the BFF accepts is still refused by the session binding", async ({ context }) => {
  const w = createWorld({ label: "Csrf2" });
  worlds.push(w);
  const viewer = w.addMember("viewer");
  await signInWithPassword(context, w.email);
  const forged = "Z".repeat(43);
  await context.addCookies([{ name: "bp_csrf", value: forged, url: BASE_URL, sameSite: "Lax" }]);

  const response = await context.request.delete(bffUrl(w.orgId, `/members/${membershipId(viewer, w.orgId)}`), { headers: { "x-csrf-token": forged } });

  expect(response.status()).toBe(403);
  expect(((await response.json()) as { detail: { code: string } }).detail.code).toBe("csrf_failed");
  expect(testRow(`select count(*) from organization_users where organization_id = ${sql(w.orgId)}`)).toBe("2");
});

test("removing a member revokes none of their sessions, and they can still use their other organization", async ({ browser, context }) => {
  const w = createWorld({ label: "Sessions" });
  const elsewhere = createWorld({ label: "Elsewhere" });
  worlds.push(w, elsewhere);
  const victim = w.addMember("employee");
  const victimId = testRow(`select id from users where email = ${sql(victim)}`);
  testRow(`insert into organization_users (organization_id, user_id, role) values (${sql(elsewhere.orgId)}, ${sql(victimId)}, 'viewer')`);

  const person = await browser.newContext({ baseURL: BASE_URL });
  await signInWithPassword(person, victim);
  const sessions = testRow(`select count(*) from auth_sessions where user_id = ${sql(victimId)} and revoked_at is null`);
  expect(Number(sessions)).toBeGreaterThan(0);

  await signInWithPassword(context, w.email);
  expect((await context.request.delete(bffUrl(w.orgId, `/members/${membershipId(victim, w.orgId)}`))).status()).toBe(204);

  expect(testRow(`select count(*) from auth_sessions where user_id = ${sql(victimId)} and revoked_at is null`)).toBe(sessions); // untouched
  expect((await person.request.get(bffUrl(w.orgId, "/customers"))).status()).toBe(404); // ordinary tenant resolution
  expect((await person.request.get(bffUrl(elsewhere.orgId, "/customers"))).status()).toBe(200); // same session, other organization
  await person.close();
});
