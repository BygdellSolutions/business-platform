import { expect, test } from "./fixtures";

import { createCompletedTransaction, createCustomer, createInvoiceApi, createWorld, issueInvoiceApi, signIn, type World } from "./support";

/**
 * A return case from the invoice page: opened with a quantity, a reason and a follow-up date, the goods received,
 * approved (the credit form opens prefilled), and closed by its credit note; the dashboard and the list show it while
 * it is open.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("a return is opened, received, approved and closed by its credit note", async ({ page, context }) => {
  world = createWorld({ label: "Returns" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const tx = await createCompletedTransaction(context, world.orgId, customer.id);
  const invoice = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [tx.id]));

  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  const returns = page.getByTestId("returns");
  await returns.getByTestId("open-return").click();
  await returns.getByTestId("return-quantity").first().fill("1");
  await returns.getByLabel("Reason").fill("Customer emailed: wrong size");
  await returns.getByTestId("create-return").click();

  const item = returns.getByTestId("return-case");
  await expect(item.getByTestId("return-state")).toHaveText("Requested");
  await expect(page.getByTestId("return-open")).toBeVisible();

  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("card-returns")).toContainText("1");
  await page.goto(`/o/${world.orgId}/invoices?returns=open`);
  await expect(page.getByTestId("invoice-row")).toHaveCount(1);
  await page.getByTestId("invoice-link").click();

  await item.getByLabel("Add a note").fill("Parcel on its way");
  await item.getByTestId("return-add-note").click();
  await item.getByTestId("return-received").click();
  await expect(item.getByTestId("return-state")).toHaveText("Goods received");
  await item.getByTestId("return-approve").click();

  const credits = page.getByTestId("credit-notes");
  await expect(credits.getByRole("heading", { name: "Credit note for the approved return" })).toBeVisible();
  await expect(credits.getByLabel("Reason")).toHaveValue("Return: Customer emailed: wrong size");
  await credits.getByTestId("create-credit-note").click();

  await expect(credits.getByTestId("credit-note-row")).toHaveCount(1);
  await expect(item.getByTestId("return-state")).toHaveText("Credited");
  await expect(page.getByTestId("return-open")).toHaveCount(0);
  await item.getByTestId("return-log-toggle").click();
  await expect(item.getByTestId("return-log").locator("li")).toHaveCount(5); // opened, note, received, approved, credited
});
