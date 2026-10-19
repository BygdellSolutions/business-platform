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
  await page.getByTestId("open-returns").click(); // Returns is a pop-up window beside Download PDF
  const returns = page.getByTestId("returns");
  await returns.getByTestId("open-return").click();
  await returns.getByTestId("return-quantity").first().fill("1");
  await returns.getByLabel("Reason").fill("Customer emailed: wrong size");
  await returns.getByTestId("create-return").click();

  const item = returns.getByTestId("return-case");
  await expect(item.getByTestId("return-state")).toHaveText("Requested");
  await expect(page.getByTestId("open-returns")).toHaveText("Returns (1 open)");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("return-open")).toBeVisible();

  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("card-returns")).toContainText("1");
  await page.goto(`/o/${world.orgId}/invoices?returns=open`);
  await expect(page.getByTestId("invoice-row")).toHaveCount(1);
  await expect(page.getByTestId("invoice-return")).toHaveText(/^Open · follow up \d{1,2} [A-Z][a-z]{2}( \d{4})?$/); // its own column
  await page.goto(`/o/${world.orgId}/invoices?returns=follow_up_due`);
  await expect(page.getByTestId("empty")).toBeVisible(); // the follow-up date is in a week
  await page.goto(`/o/${world.orgId}/invoices?returns=open`);
  await page.getByTestId("invoice-link").click();
  await page.getByTestId("open-returns").click();

  await item.getByLabel("Add a note").fill("Parcel on its way");
  await item.getByTestId("return-add-note").click();
  await item.getByTestId("return-received").click();
  await expect(item.getByTestId("return-state")).toHaveText("Goods received");
  await item.getByTestId("return-approve").click();

  // approving closes the Returns window and opens the Credit notes window on that return
  await expect(page.getByRole("dialog", { name: "Returns" })).toBeHidden();
  const credits = page.getByTestId("credit-notes");
  await expect(page.getByRole("dialog", { name: "Credit notes" })).toBeVisible();
  await expect(credits.getByRole("heading", { name: "Credit note for the approved return" })).toBeVisible();
  await expect(credits.getByLabel("Reason")).toHaveValue("Return: Customer emailed: wrong size");
  await credits.getByTestId("create-credit-note").click();

  await expect(credits.getByTestId("credit-note-row")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("open-credits")).toHaveText("Credit notes (1)");
  await expect(page.getByTestId("return-open")).toHaveCount(0);
  await page.getByTestId("open-returns").click();
  await expect(item.getByTestId("return-state")).toHaveText("Credited");
  await item.getByTestId("return-log-toggle").click();
  await expect(item.getByTestId("return-log").locator("li")).toHaveCount(5); // opened, note, received, approved, credited
});

test("Returns and Credit notes are buttons beside Download PDF; someone who cannot handle them sees them only when there is something to see", async ({ page, context }) => {
  world = createWorld({ label: "ReturnsButtons" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const tx = await createCompletedTransaction(context, world.orgId, customer.id);
  const invoice = await issueInvoiceApi(context, world.orgId, await createInvoiceApi(context, world.orgId, [tx.id]));

  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  const buttons = await Promise.all(["download-pdf", "open-email", "open-payments", "open-returns", "open-credits"].map(async (id) => (await page.getByTestId(id).boundingBox())!));
  for (let i = 1; i < buttons.length; i++) {
    expect(buttons[i].x).toBeGreaterThan(buttons[i - 1].x);
    expect(Math.abs(buttons[i].y - buttons[0].y)).toBeLessThan(20); // one row
  }
  await expect(page.getByTestId("returns")).toBeHidden();
  await expect(page.getByTestId("credit-notes")).toBeHidden();

  await page.getByTestId("open-returns").click();
  await expect(page.getByRole("dialog", { name: "Returns" }).getByText("No return cases")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByTestId("open-credits").click();
  await expect(page.getByRole("dialog", { name: "Credit notes" })).toBeVisible();
  await page.keyboard.press("Escape");

  // a viewer can do neither, and there is nothing to see, so neither button is offered
  await signIn(context, world.addMember("viewer"));
  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  await expect(page.getByTestId("download-pdf")).toBeVisible();
  await expect(page.getByTestId("open-returns")).toHaveCount(0);
  await expect(page.getByTestId("open-credits")).toHaveCount(0);
});
