import { expect, test } from "./fixtures";

import { createCompletedTransaction, createCustomer, createInvoiceApi, createWorld, signIn, type World } from "./support";

/**
 * Slice 10: the owner fills in contact and payment details and the documents' language in Settings; an invoice
 * created without a due date then falls due after the payment terms, and keeps the payment details it was made with.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("payment details set in Settings reach the invoice, and the terms decide its due date", async ({ page, context }) => {
  world = createWorld({ label: "Seller" });
  await signIn(context, world.email);

  await page.goto(`/o/${world.orgId}/settings`);
  await page.getByLabel("Bankgiro").fill("123-4567");
  await page.getByLabel("IBAN").fill("se45 5000 0000 0583 9825 7466");
  await page.getByLabel("Payment terms (days)").fill("30");
  await page.getByLabel("Approved for F-tax (F-skatt)").selectOption("yes");
  await page.getByLabel("Document language").selectOption("sv");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();
  await expect(page.getByLabel("IBAN")).toHaveValue("SE4550000000058398257466");

  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const tx = await createCompletedTransaction(context, world.orgId, customer.id);
  const invoice = await createInvoiceApi(context, world.orgId, [tx.id], { invoice_date: "2026-10-08" });
  expect(invoice.issuer_snapshot).toMatchObject({ schema: 3, bankgiro: "123-4567", iban: "SE4550000000058398257466", approved_for_f_tax: true, document_language: "sv" });

  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  await expect(page.getByTestId("invoice-due-date")).toHaveText("2026-11-07");
});
