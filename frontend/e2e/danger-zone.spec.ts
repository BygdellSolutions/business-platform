import { readFileSync } from "node:fs";

import { expect, test } from "./fixtures";

import { E2E_PASSWORD } from "./auth-support";
import { AUTH_E2E } from "./env";
import { bffUrl, createCompletedTransaction, createInvoiceApi, createWorld, insertCustomer, issueInvoiceApi, signIn, sql, testRow, type World } from "./support";

/**
 * The danger zone at the bottom of Settings (dev run and session run): transfer ownership, delete the organization
 * with everything in it. In the session run the password really is checked.
 */

let world: World;
test.afterEach(() => world?.cleanup());

const roleOf = (email: string, orgId: string) =>
  testRow(`select coalesce((select ou.role from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(email)} and ou.organization_id = ${sql(orgId)}), '')`);

test("the sole owner transfers ownership to a member and can then leave", async ({ page, context }) => {
  world = createWorld({ label: "Transfer" });
  const employee = world.addMember("employee");
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}/settings`);

  const zone = page.getByTestId("danger-zone");
  await expect(zone.getByTestId("sole-owner")).toBeVisible();
  const transfer = zone.getByRole("form", { name: "Transfer ownership" });
  const membership = testRow(`select ou.id from organization_users ou join users u on u.id = ou.user_id where u.email = ${sql(employee)} and ou.organization_id = ${sql(world.orgId)}`);
  await transfer.getByLabel("New owner").selectOption(membership);
  await transfer.getByLabel("Your password").fill(E2E_PASSWORD);
  await transfer.getByTestId("transfer-ownership").click();
  await expect(zone.getByTestId("transferred")).toBeVisible();
  expect(roleOf(employee, world.orgId)).toBe("owner");
  expect(roleOf(world.email, world.orgId)).toBe("admin");

  // No longer the sole owner (no longer an owner at all): leaving is offered and works.
  await page.reload();
  await zone.getByRole("form", { name: "Leave organization" }).getByLabel("Your password").fill(E2E_PASSWORD);
  await zone.getByTestId("leave-organization").click();
  await expect(page).toHaveURL(/\/$/);
  expect(roleOf(world.email, world.orgId)).toBe("");
});

test("an owner deletes the organization with an issued invoice in it; everything is gone", async ({ page, context }) => {
  world = createWorld({ label: "Delete" });
  await signIn(context, world.email);
  const customer = insertCustomer(world.orgId, "Umeå HK");
  const tx = await createCompletedTransaction(context, world.orgId, customer);
  await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [tx.id]));
  await page.goto(`/o/${world.orgId}/settings`);

  // A copy first: downloaded through the real BFF stream, and it counts for the deletion below.
  const copy = page.getByRole("form", { name: "Download a copy of your data" });
  await copy.getByLabel("Your password").fill(E2E_PASSWORD);
  const downloading = page.waitForEvent("download");
  await copy.getByTestId("download-copy").click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^export-[a-z0-9-]+-\d{4}-\d{2}-\d{2}\.zip$/);
  const archive = readFileSync((await download.path())!);
  expect(archive.subarray(0, 4).toString("latin1")).toBe("PK\u0003\u0004"); // a ZIP
  for (const name of ["manifest.json", "data/customers.jsonl", "data/invoices.jsonl", "members.jsonl"]) expect(archive.includes(Buffer.from(name)), name).toBe(true);
  expect(archive.includes(Buffer.from(".pdf"))).toBe(true); // the issued invoice's stored PDF is in it
  await expect(page.getByTestId("export-done")).toBeVisible();
  expect(testRow(`select count(*) from organizations where id = ${sql(world.orgId)} and last_export_at is not null`)).toBe("1");

  const form = page.getByRole("form", { name: "Delete organization" });
  await expect(page.getByTestId("delete-warning")).toContainText("issued invoices and their PDFs");
  await expect(form.getByTestId("delete-organization")).toBeDisabled();
  await expect(form.getByTestId("copy-made")).toBeVisible();
  await expect(form.getByTestId("without-copy")).toHaveCount(0); // a copy exists: nothing to decline
  await form.getByLabel(/Type the organization/).fill(world.name);
  await form.getByLabel("Your password").fill(E2E_PASSWORD);
  await form.getByLabel(/I understand/).check();
  await form.getByTestId("delete-organization").click();

  await expect(page).toHaveURL(/\/$/);
  expect(testRow(`select count(*) from organizations where id = ${sql(world.orgId)}`)).toBe("0");
  for (const table of ["invoices", "transactions", "customers", "organization_users", "audit_events"]) {
    expect(testRow(`select count(*) from ${table} where organization_id = ${sql(world.orgId)}`), table).toBe("0");
  }
});

test("the server refuses a deletion that neither follows a copy nor says none is wanted", async ({ context }) => {
  world = createWorld({ label: "NoCopyNoDelete" });
  await signIn(context, world.email);

  const refused = await context.request.post(bffUrl(world.orgId, "/organization/delete"), { data: { confirm_name: world.name, password: E2E_PASSWORD } });

  expect(refused.status()).toBe(409);
  expect(JSON.stringify(await refused.json())).toContain("export_required");
  expect(testRow(`select count(*) from organizations where id = ${sql(world.orgId)}`)).toBe("1");
});

test("a mistyped name deletes nothing", async ({ page, context }) => {
  world = createWorld({ label: "NoDelete" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}/settings`);
  const form = page.getByRole("form", { name: "Delete organization" });
  await form.getByLabel(/Type the organization/).fill("not the name");
  await form.getByLabel("Your password").fill(E2E_PASSWORD);
  await form.getByLabel(/I understand/).check();
  await form.getByTestId("without-copy").check();
  await form.getByTestId("delete-organization").click();
  await expect(page.getByTestId("delete-error")).toContainText("exactly as shown");
  expect(testRow(`select count(*) from organizations where id = ${sql(world.orgId)}`)).toBe("1");
});

test("session run: a wrong password refuses leaving and deleting", async ({ context }) => {
  test.skip(AUTH_E2E !== "session", "the development sign-in has no passwords");
  world = createWorld({ label: "WrongPassword" });
  const employee = world.addMember("employee");
  await signIn(context, world.email);

  const deletion = await context.request.post(bffUrl(world.orgId, "/organization/delete"), { data: { confirm_name: world.name, password: "not the password" } });
  expect(deletion.status()).toBe(403);
  expect(testRow(`select count(*) from organizations where id = ${sql(world.orgId)}`)).toBe("1");

  await context.clearCookies();
  await signIn(context, employee);
  const leave = await context.request.post(bffUrl(world.orgId, "/members/leave"), { data: { password: "not the password" } });
  expect(leave.status()).toBe(403);
  expect(roleOf(employee, world.orgId)).toBe("employee");
});
