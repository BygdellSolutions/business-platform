import { expect, test } from "./fixtures";

import { addLine, bffUrl, createCustomer, createItem, createTransaction, createWorld, signIn, type World } from "./support";

/**
 * Discounts as a person meets them: a campaign on an item, a customer's permanent discount, and a transaction line
 * that shows every step (1 000.00, then -20 %, then -10 % = 720.00), never 30 % off.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("an owner sets a campaign and a customer discount; a new line shows every step", async ({ page, context }) => {
  world = createWorld({ label: "Discounts" });
  await signIn(context, world.email);
  const item = await createItem(context, world.orgId, { name: "Horse liniment", type: "product", price_ex_vat: "1000.00", vat_rate: "25" });
  const customer = await createCustomer(context, world.orgId, "Customer A");

  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  const form = page.getByRole("form", { name: "Add discount" });
  await form.getByLabel("Discount %").fill("20");
  await form.locator("input[name=starts_on]").fill("2026-10-01");
  await form.locator("input[name=ends_on]").fill("2026-10-07");
  await form.getByTestId("add-discount").click();
  await expect(page.getByTestId("discount-row")).toHaveCount(1);
  await expect(page.getByTestId("discount-row")).toContainText("2026-10-07");

  await page.goto(`/o/${world.orgId}/customers/${customer.id}`);
  await page.getByLabel("Default discount %").fill("10");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();

  const tx = await createTransaction(context, world.orgId, { billing_customer_id: customer.id, transaction_date: "2026-10-03" });
  await addLine(context, world.orgId, tx.id, { item_id: item.id, quantity: "1" });
  await page.goto(`/o/${world.orgId}/transactions/${tx.id}`);
  await expect(page.getByTestId("line-price")).toContainText("720.00");
  await expect(page.getByTestId("discount-steps")).toHaveText(/List 1[\s ]?000\.00 · −20\.00% campaign · −10\.00% customer/);
});

test("an employee sees a customer's discount but cannot change it", async ({ page, context }) => {
  world = createWorld({ label: "DiscountRole" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Customer B");
  await context.request.patch(bffUrl(world.orgId, `/customers/${customer.id}`), { data: { default_discount_percent: "10" } });

  await context.clearCookies();
  await signIn(context, world.addMember("employee"));
  await page.goto(`/o/${world.orgId}/customers/${customer.id}`);
  await expect(page.getByTestId("discount-read-only")).toContainText("10.00 %");
  await expect(page.getByLabel("Default discount %")).toHaveCount(0);
  const forged = await context.request.patch(bffUrl(world.orgId, `/customers/${customer.id}`), { data: { default_discount_percent: "50" } });
  expect(forged.status()).toBe(403);
});
