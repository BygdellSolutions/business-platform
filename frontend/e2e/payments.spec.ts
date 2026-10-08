import { expect, test } from "./fixtures";

import { createCompletedTransaction, createCustomer, createInvoiceApi, createWorld, issueInvoiceApi, signIn, type World } from "./support";

/**
 * Manual payments: on an issued invoice a person records payments until it is paid; a mistake is reversed (never
 * deleted); the invoice list can show only what is not fully paid.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("an issued invoice is paid in two parts, a mistake is reversed, and the list filters by payment", async ({ page, context }) => {
  world = createWorld({ label: "Payments" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const tx = await createCompletedTransaction(context, world.orgId, customer.id);
  const invoice = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [tx.id]));

  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  const panel = page.getByTestId("payments-panel");
  await expect(panel.getByTestId("payment-status")).toHaveText("Unpaid");
  const gross = await panel.getByLabel(/^Amount/).inputValue();

  await panel.getByLabel(/^Amount/).fill("100.00");
  await panel.getByLabel("Method").selectOption("swish");
  await panel.getByTestId("record-payment").click();
  await expect(panel.getByTestId("payment-status")).toHaveText("Partially paid");
  await expect(panel.getByTestId("payment-row")).toHaveCount(1);

  await page.goto(`/o/${world.orgId}/invoices?payment=open`);
  await expect(page.getByTestId("invoice-row")).toHaveCount(1);
  await expect(page.getByTestId("invoice-payment")).toContainText("Partially paid");

  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  await panel.getByTestId("record-payment").click(); // the amount is prefilled with what is outstanding
  await expect(panel.getByTestId("payment-status")).toHaveText("Paid");
  await expect(panel.getByTestId("record-payment")).toHaveCount(0);

  await panel.getByTestId("reverse-payment").first().click();
  await page.getByRole("button", { name: "Yes, reverse" }).click();
  await expect(panel.getByTestId("payment-status")).toHaveText("Partially paid");
  await expect(panel.getByTestId("payment-row")).toHaveCount(3);
  await expect(panel.getByTestId("payment-row").last()).toContainText("Reversal");
  expect(gross).not.toBe("");
});
