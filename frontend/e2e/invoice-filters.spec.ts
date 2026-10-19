import { expect, test } from "./fixtures";

import { bffUrl, createCompletedTransaction, createCustomer, createInvoiceApi, createWorld, issueInvoiceApi, psql, signIn, sql, type World } from "./support";

/**
 * The invoice list's filters offer the statuses the badges show, read from the backend (nothing hard-coded on the screen), and they filter
 * by what the badge says.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("the Status filter offers the statuses an invoice can have today, and filtering by one shows the invoices with that badge", async ({ page, context }) => {
  world = createWorld({ label: "InvoiceFilters" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const make = async () => createInvoiceApi(context, world.orgId, [(await createCompletedTransaction(context, world.orgId, customer.id)).id]);
  const draft = await make();
  const unpaid = await issueInvoiceApi(context, world.orgId, await make());
  const paid = await issueInvoiceApi(context, world.orgId, await make());
  const settled = await context.request.post(bffUrl(world.orgId, `/invoices/${paid.id}/payments`), {
    data: { amount: paid.gross_amount, paid_on: new Date().toISOString().slice(0, 10), method: "bank_transfer" },
  });
  expect(settled.status(), await settled.text()).toBe(201);
  psql(
    "postgres-test",
    `insert into invoice_emails (organization_id, invoice_id, to_email, sender_address, subject, pdf_sha256, status) values (${sql(world.orgId)}, ${sql(unpaid.id)}, 'a@example.test', 'o@invoices.bygdell.solutions', 's', '${"0".repeat(64)}', 'sent')`,
  );

  await page.goto(`/o/${world.orgId}/invoices`);
  const status = page.locator('select[name="state"]');
  // the words are the badges' words, in the order an invoice goes through them; the old "Draft / Issued" pair is gone
  expect(await status.locator("option").allTextContents()).toEqual(["All", "Draft", "Issued (all but drafts)", "Unpaid", "Partially paid", "Paid", "Credited", "Partly credited"]);
  expect(await page.locator('select[name="sent"]').locator("option").allTextContents()).toEqual(["All", "Sent to the customer", "Not sent yet"]);
  expect(await page.locator('select[name="payment"]').locator("option").allTextContents()).toEqual(["All", "Not fully paid", "Overdue", "Not yet due", "Refund due"]);
  await expect(page.locator('select[name="credit"]')).toHaveCount(0); // folded into Status: Credited, Partly credited

  const rows = page.getByTestId("invoice-row");
  await expect(rows).toHaveCount(3);

  await status.selectOption("paid");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/state=paid/);
  await expect(rows).toHaveCount(1);
  await expect(rows.first().getByTestId("invoice-status")).toHaveText("Paid");
  await expect(page.locator('select[name="state"]')).toHaveValue("paid"); // the choice is shown again after the page loads

  await page.locator('select[name="state"]').selectOption("draft");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first().getByTestId("invoice-status")).toHaveText("Draft");
  expect(draft.id).toBeTruthy();

  await page.locator('select[name="state"]').selectOption("unpaid");
  await page.locator('select[name="sent"]').selectOption("sent");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first().getByTestId("invoice-sent")).toBeVisible();

  await page.locator('select[name="sent"]').selectOption("not_sent");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(rows).toHaveCount(0); // the only unpaid invoice has been sent
});

test("the dashboard cards open the list with the right status chosen", async ({ page, context }) => {
  world = createWorld({ label: "InvoiceFiltersLinks" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}/invoices?state=draft`);
  await expect(page.locator('select[name="state"]')).toHaveValue("draft");
  await page.goto(`/o/${world.orgId}/invoices?state=issued&date_from=2026-01-01&date_to=2026-12-31`);
  await expect(page.locator('select[name="state"]')).toHaveValue("issued");
  await page.goto(`/o/${world.orgId}/invoices?state=nonsense`);
  await expect(page.locator('select[name="state"]')).toHaveValue(""); // a word that is not a status is ignored, not shown as one
});
