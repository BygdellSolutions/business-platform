import { expect, test } from "./fixtures";

import { addLine, bffUrl, createCustomer, createHorse, createItem, createTransaction, createWorld, ifMatch, signIn, sql, testRow, type World } from "./support";

/**
 * A viewer reads customers, catalog items, horses and transactions, and is offered no control that
 * changes them; an employee is. Hiding controls is presentation only: the forged requests below go
 * straight to the BFF as a viewer and are refused by FastAPI, which is the real authority.
 */

let world: World;
test.afterEach(() => world?.cleanup());

/** A world with one record of each kind, made by the owner through the API. */
async function build(context: Parameters<typeof signIn>[0]) {
  world = createWorld({ label: "RecordRoles" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const item = await createItem(context, world.orgId, { name: "Horse massage", price_ex_vat: "850.00" });
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: customer.id });
  const draft = await createTransaction(context, world.orgId, { billing_customer_id: customer.id, transaction_date: "2026-10-01" });
  const line = await addLine(context, world.orgId, draft.id, { item_id: item.id, quantity: "1" });
  return { customer, item, horse, draft, line };
}

/** Everything the organization holds, so "nothing changed" is checked on the database itself. */
const fingerprint = () =>
  testRow(
    ["customers", "items", "horses", "transactions", "transaction_lines"]
      .map((table) => `(select coalesce(md5(string_agg(to_jsonb(t)::text, ',' order by t.id)), '-') from ${table} t where organization_id = ${sql(world.orgId)})`)
      .join(" || '|' || ")
      .replace(/^/, "select "),
  );

test.describe("viewer: reads records but cannot change them", () => {
  test("lists offer no create links, records are shown without forms, create pages refuse", async ({ page, context }) => {
    const s = await build(context);
    await signIn(context, world.addMember("viewer"));

    for (const [area, testId] of [["customers", "new-customer"], ["catalog", "new-item"], ["horses", "new-horse"], ["transactions", "new-transaction"]] as const) {
      await page.goto(`/o/${world.orgId}/${area}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByTestId(testId)).toHaveCount(0);

      await page.goto(`/o/${world.orgId}/${area}/new`);
      await expect(page.getByTestId("not-allowed")).toBeVisible();
      await expect(page.locator("main form")).toHaveCount(0);
    }

    for (const path of [`customers/${s.customer.id}`, `catalog/${s.item.id}`, `horses/${s.horse.id}`]) {
      await page.goto(`/o/${world.orgId}/${path}`);
      await expect(page.getByTestId("record-details")).toBeVisible();
      await expect(page.locator("main form")).toHaveCount(0);
      await expect(page.locator("main").getByRole("button")).toHaveCount(0);
    }
    await expect(page.getByTestId("record-details")).toContainText("Anna Andersson"); // the horse's owner

    await page.goto(`/o/${world.orgId}/transactions/${s.draft.id}`);
    await expect(page.getByTestId("role-note")).toBeVisible();
    await expect(page.getByTestId("line-row")).toHaveCount(1);
    await expect(page.getByTestId("transaction-editor").getByRole("button")).toHaveCount(0);
  });

  test("forged requests straight to the BFF are refused by the backend and change nothing", async ({ context }) => {
    const s = await build(context);
    await signIn(context, world.addMember("viewer"));
    const before = fingerprint();
    const tx = `/transactions/${s.draft.id}`;

    const statuses = [
      (await context.request.post(bffUrl(world.orgId, "/customers"), { data: { customer_type: "person", name: "Forged" } })).status(),
      (await context.request.patch(bffUrl(world.orgId, `/customers/${s.customer.id}`), { data: { name: "Forged" } })).status(),
      (await context.request.delete(bffUrl(world.orgId, `/items/${s.item.id}`))).status(),
      (await context.request.patch(bffUrl(world.orgId, `/horses/${s.horse.id}`), { data: { name: "Forged" } })).status(),
      (await context.request.post(bffUrl(world.orgId, "/transactions"), { data: { billing_customer_id: s.customer.id } })).status(),
      (await context.request.post(bffUrl(world.orgId, `${tx}/lines`), { data: { item_id: s.item.id, quantity: "1" } })).status(),
      (await context.request.patch(bffUrl(world.orgId, `${tx}/lines/${s.line.id}`), { data: { quantity: "2" }, headers: ifMatch(s.line.version) })).status(),
      (await context.request.post(bffUrl(world.orgId, `${tx}/complete`), { headers: ifMatch(s.draft.version + 1) })).status(),
      (await context.request.delete(bffUrl(world.orgId, tx), { headers: ifMatch(s.draft.version + 1) })).status(),
    ];

    expect(statuses).toEqual(Array(statuses.length).fill(403));
    expect(fingerprint()).toBe(before);
    // ... while reading is allowed.
    expect((await context.request.get(bffUrl(world.orgId, `/customers/${s.customer.id}`))).status()).toBe(200);
    expect((await context.request.get(bffUrl(world.orgId, tx))).status()).toBe(200);
  });
});

test("employee: is offered the controls and its writes go through (control for the viewer specs)", async ({ page, context }) => {
  const s = await build(context);
  await signIn(context, world.addMember("employee"));

  await page.goto(`/o/${world.orgId}/customers`);
  await expect(page.getByTestId("new-customer")).toBeVisible();
  await page.goto(`/o/${world.orgId}/customers/${s.customer.id}`);
  await expect(page.getByTestId("submit")).toBeVisible();
  await page.goto(`/o/${world.orgId}/transactions/${s.draft.id}`);
  await expect(page.getByTestId("invoice-order")).toBeVisible();
  await expect(page.getByTestId("role-note")).toHaveCount(0);

  const renamed = await context.request.patch(bffUrl(world.orgId, `/customers/${s.customer.id}`), { data: { name: "Anna A." } });
  expect(renamed.status()).toBe(200);
});
