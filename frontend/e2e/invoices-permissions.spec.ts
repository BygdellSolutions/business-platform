import { expect, test } from "./fixtures";

import { bffUrl, createCompletedTransaction, createInvoiceApi, createWorld, getInvoiceApi, ifMatch, insertCustomer, issueInvoiceApi, signIn, sql, testRow, type RoleName, type World } from "./support";

/**
 * Who may do what with invoices, as the browser and the BFF show it. Every member can READ; owner,
 * admin and accountant can change. Hiding controls is presentation only: the forged requests below
 * go straight to the BFF as a reader and are refused by FastAPI, which is the real authority.
 */

let world: World;
test.afterEach(() => world?.cleanup());

/** A world with a draft and an issued invoice (built by the owner through the API). */
async function build(context: Parameters<typeof signIn>[0]) {
  world = createWorld({ label: "InvoiceRoles" });
  await signIn(context, world.email);
  const customer = insertCustomer(world.orgId, "Umeå HK");
  const t1 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-01" });
  const t2 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-02" });
  const t3 = await createCompletedTransaction(context, world.orgId, customer, { date: "2026-10-03" });
  const draft = await createInvoiceApi(context, world.orgId, [t1.id], { description: "Draft" });
  const issued = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [t2.id]));
  return { customer, t1, t2, t3, draft, issued };
}

const state = (id: string) => testRow(`select status || '|' || version || '|' || coalesce(description, '-') from invoices where id = ${sql(id)}`);
const invoiceCount = () => testRow(`select count(*) from invoices where organization_id = ${sql(world.orgId)}`);

for (const role of ["employee", "viewer"] as RoleName[]) {
  test.describe(`${role}: reads invoices but cannot change them`, () => {
    test("sees the draft and the issued invoice and the list, with no control that changes anything", async ({ page, context }) => {
      const s = await build(context);
      await signIn(context, world.addMember(role));

      await page.goto(`/o/${world.orgId}/invoices/${s.draft.id}`);
      await expect(page.getByTestId("invoice-heading")).toHaveText("Draft invoice (no number yet)");
      await expect(page.getByTestId("invoice-description")).toHaveText("Draft");
      await expect(page.getByTestId("no-mutation")).toBeVisible();
      for (const control of ["issue", "delete-draft", "edit-details"]) await expect(page.getByTestId(control)).toHaveCount(0);
      await expect(page.getByTestId("invoice-view").getByRole("button")).toHaveCount(0);

      await page.goto(`/o/${world.orgId}/invoices/${s.issued.id}`);
      await expect(page.getByTestId("invoice-number")).toHaveText("1");

      await page.goto(`/o/${world.orgId}/invoices`);
      await expect(page.getByTestId("invoice-row")).toHaveCount(2);
      await expect(page.getByTestId("new-invoice")).toHaveCount(0);
    });

    test("sees what is waiting to be invoiced but cannot select or create", async ({ page, context }) => {
      await build(context);
      await signIn(context, world.addMember(role));

      await page.goto(`/o/${world.orgId}/invoices/new`);
      await expect(page.getByTestId("read-only")).toBeVisible();
      await expect(page.getByTestId("eligible-row")).toHaveCount(1); // the one transaction that is on no invoice
      await expect(page.getByTestId("select-transaction")).toHaveCount(0);
      await expect(page.getByTestId("submit")).toHaveCount(0);
    });

    test("forged requests straight to the BFF are refused by the backend and change nothing", async ({ context }) => {
      const s = await build(context);
      await signIn(context, world.addMember(role));
      const before = state(s.draft.id);
      const count = invoiceCount();

      const create = await context.request.post(bffUrl(world.orgId, "/invoices"), { data: { transaction_ids: [s.t3.id] } });
      const edit = await context.request.patch(bffUrl(world.orgId, `/invoices/${s.draft.id}`), { data: { description: "Forged" }, headers: ifMatch(s.draft.version) });
      const issue = await context.request.post(bffUrl(world.orgId, `/invoices/${s.draft.id}/issue`), { headers: ifMatch(s.draft.version) });
      const remove = await context.request.delete(bffUrl(world.orgId, `/invoices/${s.draft.id}`), { headers: ifMatch(s.draft.version) });

      expect([create.status(), edit.status(), issue.status(), remove.status()]).toEqual([403, 403, 403, 403]);
      expect(state(s.draft.id)).toBe(before);
      expect(invoiceCount()).toBe(count);
      // ... while reading is allowed.
      expect((await context.request.get(bffUrl(world.orgId, `/invoices/${s.draft.id}`))).status()).toBe(200);
      expect((await context.request.get(bffUrl(world.orgId, "/invoiceable-transactions"))).status()).toBe(200);
    });
  });
}

for (const role of ["accountant", "admin"] as RoleName[]) {
  test(`${role}: gets the controls and can issue`, async ({ page, context }) => {
    const s = await build(context);
    await signIn(context, world.addMember(role));

    await page.goto(`/o/${world.orgId}/invoices/${s.draft.id}`);
    for (const control of ["issue", "delete-draft", "edit-details"]) await expect(page.getByTestId(control)).toBeVisible();
    await page.getByTestId("issue").click();
    await page.getByTestId("issue-confirm").click();
    await expect(page.getByTestId("invoice-status")).toHaveText("Issued");
    await expect(page.getByTestId("invoice-number")).toHaveText("2");

    await page.goto(`/o/${world.orgId}/invoices/new`);
    await expect(page.getByTestId("select-transaction")).toHaveCount(1);
    await expect(page.getByTestId("read-only")).toHaveCount(0);
  });
}

test("the role of the user in THIS organization decides, not a role somewhere else", async ({ page, context }) => {
  const s = await build(context);
  const other = createWorld({ label: "InvoiceRolesOther" });
  try {
    // The same person is an owner elsewhere and only an employee here.
    const person = world.addMember("employee");
    testRow(`update users set max_owned_organizations = max_owned_organizations + 1 where email = ${sql(person)}; insert into organization_users (organization_id, user_id, role) select ${sql(other.orgId)}, id, 'owner' from users where email = ${sql(person)}`); // owning a second organization needs the allowance
    await signIn(context, person);

    await page.goto(`/o/${world.orgId}/invoices/${s.draft.id}`);
    await expect(page.getByTestId("no-mutation")).toBeVisible();
    await expect(page.getByTestId("issue")).toHaveCount(0);
    const forged = await context.request.post(bffUrl(world.orgId, `/invoices/${s.draft.id}/issue`), { headers: ifMatch(s.draft.version) });
    expect(forged.status()).toBe(403);
    expect((await getInvoiceApi(context, world.orgId, s.draft.id)).status).toBe("draft");
  } finally {
    other.cleanup();
  }
});

test("a forged organization header or cookie does not change which organization the BFF acts for", async ({ context }) => {
  const s = await build(context);
  const other = createWorld({ label: "InvoiceHeaderTarget" });
  try {
    const otherCustomer = insertCustomer(other.orgId, "Other Co");
    testRow(`update users set max_owned_organizations = max_owned_organizations + 1 where email = ${sql(world.email)}; insert into organization_users (organization_id, user_id, role) select ${sql(other.orgId)}, id, 'owner' from users where email = ${sql(world.email)}`); // owning a second organization needs the allowance
    await signIn(context, world.email);
    const mine = await context.request.get(bffUrl(world.orgId, "/invoices"), { headers: { "x-organization-id": other.orgId, "x-dev-user-email": other.email } });
    expect(mine.status()).toBe(200);
    const ids = ((await mine.json()) as { id: string }[]).map((row) => row.id);
    expect(ids.sort()).toEqual([s.draft.id, s.issued.id].sort()); // my organization's invoices, whatever the headers said
    expect(otherCustomer).toBeTruthy();
  } finally {
    other.cleanup();
  }
});
