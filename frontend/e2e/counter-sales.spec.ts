import { expect, test } from "./fixtures";

import { bffUrl, createCustomer, createTransaction, createWorld, signIn, testRow, sql, type World } from "./support";

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
    await expect(page.getByTestId("order-invoice")).toHaveText("Draft invoice"); // the link stays at the top of the order
  }
  await page.getByTestId("open-draft-invoice").click();
  await expect(page.getByTestId("invoice-order-link")).toHaveCount(2); // both orders, one draft
  await expect(page.getByTestId("invoice-order-link")).toHaveText(["Order 1001", "Order 1002"]);
  await page.getByTestId("invoice-order-link").first().click();
  await expect(page.getByTestId("record-name")).toContainText("Order 1001");
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

// A real 1x1 PNG: enough for the receipt to carry a picture.
const TINY_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

test("a receipt is emailed from a pop-up window beside Download receipt, and it carries the organization's logo", async ({ page, context }) => {
  world = createWorld({ label: "ReceiptEmail" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson", { email: "anna@example.test" });
  const order = await createTransaction(context, world.orgId, { billing_customer_id: anna.id, lines: [LINE] });
  const saved = await context.request.post(bffUrl(world.orgId, "/organization/logo"), { data: { data_base64: TINY_PNG } });
  expect(saved.status(), await saved.text()).toBe(200); // the logo really is kept

  await page.goto(`/o/${world.orgId}/transactions/${order.id}`);
  await page.getByTestId("pay-now").click();
  await page.getByTestId("pay-card").click();
  await expect(page.getByTestId("paid-at-counter")).toContainText("Receipt 1001");

  // the button sits beside Download receipt, and the window holds the form
  const [download, email] = await Promise.all(["download-pdf", "open-receipt-email"].map(async (id) => (await page.getByTestId(id).boundingBox())!));
  expect(Math.abs(download.y - email.y)).toBeLessThan(20);
  expect(download.x).toBeLessThan(email.x);
  await expect(page.getByTestId("email-receipt")).toBeHidden();
  await page.getByTestId("open-receipt-email").click();
  const dialog = page.getByRole("dialog", { name: "Send receipt by email" });
  await expect(dialog.getByLabel("Send to")).toHaveValue("anna@example.test"); // the customer's address
  await expect(dialog.getByLabel("Subject")).toHaveValue(/^(Kvitto|Receipt) 1001$/);
  await dialog.getByTestId("send-invoice-email").click();
  // no mail service is configured in the test setup: the person is told, and the attempt is kept
  await expect(dialog.getByTestId("email-failed")).toBeVisible();
  await expect(dialog.getByTestId("invoice-email")).toHaveCount(1);
  expect(testRow(`select status || '|' || error || '|' || to_email from receipt_emails where transaction_id = ${sql(order.id)}`)).toBe("failed|not_configured|anna@example.test");

  // the receipt itself, as the download makes it, has the logo
  const pdf = await context.request.get(bffUrl(world.orgId, `/invoices/receipts/${order.id}/pdf`));
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).includes(Buffer.from("/Subtype /Image"))).toBe(true);
});
