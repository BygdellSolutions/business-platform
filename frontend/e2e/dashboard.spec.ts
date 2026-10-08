import { expect, test } from "./fixtures";

import { createCompletedTransaction, createCustomer, createInvoiceApi, createTransaction, createWorld, issueInvoiceApi, organizationToday, signIn, type World } from "./support";

/**
 * Slice 13: the owner's dashboard shows what needs doing and how the month is going, for this organization only, and
 * every card leads to the list where the work is done.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("the dashboard counts drafts, sales ready to invoice and this month's sales, and links to them", async ({ page, context }) => {
  world = createWorld({ label: "Dashboard" });
  await signIn(context, world.email);
  const today = await organizationToday(context, world.orgId);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  await createTransaction(context, world.orgId, { billing_customer_id: customer.id });
  await createCompletedTransaction(context, world.orgId, customer.id, { date: today });

  await page.goto(`/o/${world.orgId}`);

  await expect(page.getByTestId("card-drafts-count")).toHaveText("1");
  await expect(page.getByTestId("card-ready-count")).toHaveText("1");
  await expect(page.getByTestId("card-completed-count")).toHaveText("1");
  await expect(page.getByTestId("card-completed-amount")).toHaveCount(1);
  await expect(page.getByTestId("card-past-due-count")).toHaveText("0");
  await expect(page.getByTestId("card-out-of-stock")).toHaveCount(0); // no product tracks stock: no stock section

  await page.getByTestId("card-drafts").click();
  await expect(page).toHaveURL(new RegExp(`/o/${world.orgId}/transactions\\?status=draft`));
});

test("the month can be chosen, and pending shows what is not yet paid", async ({ page, context }) => {
  world = createWorld({ label: "DashboardMonth" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const today = await organizationToday(context, world.orgId);
  const tx = await createCompletedTransaction(context, world.orgId, customer.id, { date: today });
  await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [tx.id], { invoice_date: today }));

  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("card-unpaid-count")).toHaveText("1");
  await expect(page.getByTestId("card-not-yet-due-count")).toHaveText("1");
  await expect(page.getByTestId("card-invoiced-count")).toHaveText("1");
  await expect(page.getByTestId("month-heading")).toContainText("This month");
  await expect(page.getByTestId("next-month")).toHaveCount(0);

  await page.getByTestId("previous-month").click();
  await expect(page.getByTestId("month-heading")).toContainText("Month ·");
  await expect(page.getByTestId("card-invoiced-count")).toHaveText("0"); // last month had none
  await expect(page.getByTestId("card-unpaid-count")).toHaveText("1"); // pending is always now

  await page.goto(`/o/${world.orgId}?month=2999-01`); // the future: shown as the current month
  await expect(page.getByTestId("month-heading")).toContainText("This month");
});
