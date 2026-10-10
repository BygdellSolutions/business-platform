import { expect, test } from "./fixtures";

import { createCompletedTransaction, createCustomer, createInvoiceApi, createItem, createWorld, issueInvoiceApi, signIn, type World } from "./support";

/**
 * A customer's page shows the customer's orders (with their invoice) and what was bought besides services, so
 * products are visible from the customer too.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("the customer's orders and the products bought are listed on the customer's page", async ({ page, context }) => {
  world = createWorld({ label: "Customer orders" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const spray = await createItem(context, world.orgId, { name: "Fly spray", type: "product", unit: "pcs", price_ex_vat: "150.00" });
  const invoiced = await createCompletedTransaction(context, world.orgId, customer.id, {
    date: "2026-10-01",
    lines: [{ item_id: spray.id, quantity: "2" }],
  });
  await createCompletedTransaction(context, world.orgId, customer.id, {
    date: "2026-10-03",
    lines: [{ description: "Delivery", unit: "trip", quantity: "1", unit_price_ex_vat: "90.00", vat_rate: "25" }],
  });
  const invoice = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [invoiced.id]));

  await page.goto(`/o/${world.orgId}/customers/${customer.id}`);

  const orders = page.getByTestId("customer-orders");
  await expect(orders.getByTestId("customer-order")).toHaveCount(2);
  await expect(orders.getByTestId("customer-order").first()).toContainText("2026-10-03");
  await expect(orders.getByTestId("customer-order-invoice").first()).toHaveText("Not invoiced yet");
  await expect(orders.getByTestId("customer-order-invoice").last()).toHaveText(`Invoice ${invoice.number_text}`);

  const bought = page.getByTestId("customer-bought");
  await expect(bought.getByTestId("bought-row")).toHaveCount(2);
  await expect(bought.getByTestId("bought-row").last()).toContainText("Fly spray");
  await bought.getByRole("link", { name: "Fly spray" }).click();
  await expect(page).toHaveURL(new RegExp(`/catalog/${spray.id}$`));
});
