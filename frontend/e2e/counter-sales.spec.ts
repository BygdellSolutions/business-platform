import { expect, test } from "./fixtures";

import { createCustomer, createTransaction, createWorld, signIn, testRow, sql, type World } from "./support";

/** A draft order is either paid now (completed + paid, with a receipt) or invoiced (onto a draft invoice). 2026-10-10. */
let world: World;
test.afterEach(() => world?.cleanup());

const LINE = { description: "Massage", unit: "h", quantity: "1", unit_price_ex_vat: "800.00", vat_rate: "25.00" };

test("paid now: choose how, the order is paid with a receipt, and the dashboard counts it", async ({ page, context }) => {
  world = createWorld({ label: "Counter" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const order = await createTransaction(context, world.orgId, { billing_customer_id: anna.id, lines: [LINE] });

  await page.goto(`/o/${world.orgId}/transactions/${order.id}`);
  await page.getByTestId("pay-now").click();
  await page.getByTestId("pay-swish").click();
  await expect(page.getByTestId("tx-status")).toHaveText("Paid");
  await expect(page.getByTestId("paid-at-counter")).toContainText("Paid by Swish · Receipt 1001");
  await expect(page.getByTestId("reopen")).toHaveCount(0);
  await expect(page.getByTestId("cancel")).toHaveCount(0);
  expect(testRow(`select status || '|' || payment_method || '|' || receipt_number from transactions where id = ${sql(order.id)}`)).toBe("completed|swish|1001");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download receipt" }).click();
  expect((await download).suggestedFilename()).toBe("receipt-1001.pdf");

  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("card-counter-sales")).toContainText("1");
  await expect(page.getByTestId("card-counter-sales")).toContainText("1000.00 SEK");
});

test("invoice: the order goes onto the customer's draft invoice, and the next one joins it", async ({ page, context }) => {
  world = createWorld({ label: "Invoice" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const first = await createTransaction(context, world.orgId, { billing_customer_id: anna.id, lines: [LINE] });
  const second = await createTransaction(context, world.orgId, { billing_customer_id: anna.id, lines: [LINE] });

  for (const order of [first, second]) {
    await page.goto(`/o/${world.orgId}/transactions/${order.id}`);
    await page.getByTestId("invoice-order").click();
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");
    await expect(page.getByTestId("open-draft-invoice")).toBeVisible();
  }
  await page.getByTestId("open-draft-invoice").click();
  await expect(page.getByTestId("source")).toHaveCount(2); // both orders, one draft
  expect(testRow(`select count(*) from invoices where organization_id = ${sql(world.orgId)} and status = 'draft'`)).toBe("1");
});

test("a walk-in customer is chosen in one click and can only pay now", async ({ page, context }) => {
  world = createWorld({ label: "Walk-in" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}/transactions/new`);
  await page.getByTestId("walk-in-customer").click();
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("walk-in-hint")).toBeVisible();
  await expect(page.getByTestId("invoice-order")).toHaveCount(0);
  await expect(page.getByTestId("pay-now")).toBeVisible();
});
