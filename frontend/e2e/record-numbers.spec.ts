import { expect, test } from "./fixtures";

import { createCustomer, createHorse, createItem, createSupplier, createTransaction, createWorld, insertCustomer, signIn, type World } from "./support";

/** Record numbers (2026-10-10): orders count from 1001, customers, suppliers, horses and catalog items from 1. */
let world: World;
test.afterEach(() => world?.cleanup());

test("every record gets its organization's next number, shown in its list and on its page, and found by search", async ({ page, context }) => {
  world = createWorld({ label: "Numbers" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  insertCustomer(world.orgId, "Bo Raw"); // inserted by SQL: the database numbers it all the same
  const supplier = await createSupplier(context, world.orgId, "Horse Supplies AB");
  const item = await createItem(context, world.orgId, { name: "Horse massage" });
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: anna.id });
  const first = await createTransaction(context, world.orgId, { billing_customer_id: anna.id });
  const second = await createTransaction(context, world.orgId, { billing_customer_id: anna.id });

  await page.goto(`/o/${world.orgId}/customers`);
  await expect(page.getByTestId("record-number")).toHaveText(["1", "2"]);
  await page.goto(`/o/${world.orgId}/customers?q=2`);
  await expect(page.getByTestId("customer-row")).toHaveCount(1);
  await expect(page.getByTestId("customer-row")).toContainText("Bo Raw");

  await page.goto(`/o/${world.orgId}/customers/${anna.id}`);
  await expect(page.getByTestId("record-number")).toHaveText("Customer no. 1");
  await page.goto(`/o/${world.orgId}/suppliers/${supplier.id}`);
  await expect(page.getByTestId("record-number")).toHaveText("Supplier no. 1");
  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  await expect(page.getByTestId("record-number")).toHaveText("Item no. 1");
  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  await expect(page.getByTestId("record-number")).toHaveText("Horse no. 1");

  await page.goto(`/o/${world.orgId}/transactions/${second.id}`);
  await expect(page.getByTestId("record-name")).toContainText("Order 1002 ·");
  await page.goto(`/o/${world.orgId}/transactions`);
  await expect(page.getByTestId("record-number")).toHaveText(["1002", "1001"]); // newest first
  await page.goto(`/o/${world.orgId}/transactions/${first.id}`);
  await expect(page.getByTestId("record-name")).toContainText("Order 1001 ·");
});
