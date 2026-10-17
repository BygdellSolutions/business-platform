import { expect, test } from "./fixtures";

import { createCompletedTransaction, createCustomer, createInvoiceApi, createWorld, issueInvoiceApi, signIn, type World } from "./support";

/**
 * Credit notes from the invoice page: a paid invoice is credited in full, the invoice says Credited with a refund due,
 * the refund is recorded, the credit note's PDF downloads, and the list finds credited invoices.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("a paid invoice is credited in full, the refund is recorded, and the credit note has its own PDF", async ({ page, context }) => {
  world = createWorld({ label: "Credit notes" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const tx = await createCompletedTransaction(context, world.orgId, customer.id);
  const invoice = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [tx.id]));

  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  const payments = page.getByTestId("payments-panel");
  await payments.getByTestId("record-payment").click(); // prefilled with the whole amount
  await expect(payments.getByTestId("payment-status")).toHaveText("Paid");

  const credits = page.getByTestId("credit-notes");
  await expect(credits.getByTestId("no-credit-notes")).toBeVisible();
  await credits.getByTestId("open-credit").click();
  await credits.getByTestId("credit-all").click();
  await credits.getByTestId("create-credit-note").click();
  await expect(credits.getByRole("alert").or(credits.getByTestId("error-reason"))).toBeVisible(); // a reason is required
  await credits.getByLabel("Reason").fill("Customer cancelled");
  await credits.getByTestId("create-credit-note").click();

  await expect(credits.getByTestId("credit-note-row")).toHaveCount(1);
  await expect(credits.getByTestId("credit-note-row")).toContainText("Customer cancelled");
  await expect(page.getByTestId("invoice-status")).toHaveText("Credited");
  await expect(credits.getByText("Everything on this invoice is credited.")).toBeVisible();
  await expect(payments.getByTestId("refund-due-amount")).toBeVisible();

  const download = page.waitForEvent("download");
  await credits.getByTestId("download-pdf").click();
  expect((await download).suggestedFilename()).toMatch(/^credit-note-\d+\.pdf$/);

  await page.goto(`/o/${world.orgId}/invoices?payment=refund_due`);
  await expect(page.getByTestId("invoice-row")).toHaveCount(1);
  await expect(page.getByTestId("refund-due")).toBeVisible();

  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  await payments.getByTestId("record-refund").click(); // prefilled with the refund due
  await expect(payments.getByTestId("refund-due-amount")).toHaveCount(0);
  await expect(payments.getByTestId("payment-row").last()).toContainText("Refund");

  await page.goto(`/o/${world.orgId}/invoices?credit=credited`);
  await expect(page.getByTestId("invoice-row")).toHaveCount(1);
  // The overview shows how much was credited and paid back, not only that it happened.
  const gross = await page.getByTestId("invoice-gross").textContent();
  await expect(page.getByTestId("invoice-credited")).toHaveText(gross ?? "");
  await expect(page.getByTestId("invoice-refunded")).toHaveText(gross ?? "");
  await expect(page.getByTestId("invoice-paid")).toHaveText("0.00"); // paid, then all of it paid back
  await expect(page.getByTestId("invoice-outstanding")).toHaveText("0.00");
  await page.goto(`/o/${world.orgId}/invoices?payment=refund_due`);
  await expect(page.getByTestId("empty")).toBeVisible();
});
