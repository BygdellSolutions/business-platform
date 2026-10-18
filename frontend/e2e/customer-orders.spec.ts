import { expect, test } from "./fixtures";

import { bffUrl, createCompletedTransaction, createCustomer, createInvoiceApi, createItem, createTransaction, createWorld, ifMatch, issueInvoiceApi, signIn, type World } from "./support";

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
  // an order paid directly at the counter (a draft with lines, paid now with a card)
  const counter = await createTransaction(context, world.orgId, {
    billing_customer_id: customer.id,
    transaction_date: "2026-10-04",
    lines: [{ description: "Brush", unit: "pcs", quantity: "1", unit_price_ex_vat: "100.00", vat_rate: "25" }],
  });
  const paying = await context.request.post(bffUrl(world.orgId, `/transactions/${counter.id}/pay-now`), { headers: ifMatch(counter.version), data: { method: "card" } });
  expect(paying.status(), await paying.text()).toBe(200);

  await page.goto(`/o/${world.orgId}/customers/${customer.id}`);

  await expect(page.getByTestId("open-orders")).toContainText("Orders (3)");
  await page.getByTestId("open-orders").click();
  const orders = page.getByTestId("customer-orders");
  await expect(orders.getByTestId("customer-order")).toHaveCount(3);
  await expect(orders.getByTestId("customer-order-invoice").filter({ hasText: "Not invoiced yet" })).toHaveCount(1);
  await expect(orders.getByTestId("customer-order-invoice").filter({ hasText: `Invoice ${invoice.number_text}` })).toHaveCount(1);
  await expect(orders.getByTestId("customer-order-paid")).toContainText("Paid directly · Card · receipt"); // paid at the counter is shown too
  const columns = async (section: string) => (await page.getByTestId(section).getByRole("columnheader").allTextContents()).map((text) => text.replace(/[▲▼↕]/g, "").trim());
  expect((await columns("customer-orders")).slice(0, 3)).toEqual(["Order no.", "Date", "Status"]);

  await page.keyboard.press("Escape");
  await page.getByTestId("open-bought").click();
  const bought = page.getByTestId("customer-bought");
  await expect(bought.getByTestId("bought-row")).toHaveCount(3);
  await expect(bought.getByTestId("bought-row").filter({ hasText: "Fly spray" })).toHaveCount(1);
  expect((await columns("customer-bought")).slice(0, 3)).toEqual(["Order no.", "Date", "Status"]); // the same first columns as the orders
  await expect(bought.getByTestId("bought-row").first().getByTestId("order-number")).toHaveText(/^\d{4,}$/); // each line says which order it is on
  await bought.getByRole("link", { name: "Fly spray" }).click();
  await expect(page).toHaveURL(new RegExp(`/catalog/${spray.id}$`));
});
