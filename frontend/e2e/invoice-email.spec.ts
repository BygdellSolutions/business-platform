import { expect, test } from "./fixtures";

import { bffUrl, createCompletedTransaction, createCustomer, createInvoiceApi, createWorld, issueInvoiceApi, signIn, sql, testRow, type World } from "./support";

/**
 * Emailing an issued invoice: the form is on the invoice page (not on a draft), the customer's address is filled in, an attempt is listed
 * and logged whatever came of it (here no mail service is configured, so it is reported as not sent), and only the people who do the
 * books can send. Another organization's invoice is not reachable.
 */

let world: World;
let other: World;
test.afterEach(() => {
  world?.cleanup();
  other?.cleanup();
});

async function issuedInvoice(context: Parameters<typeof createCustomer>[0], orgId: string) {
  const customer = await createCustomer(context, orgId, "Anna Andersson", { email: "anna@example.test" });
  const tx = await createCompletedTransaction(context, orgId, customer.id);
  const draft = await createInvoiceApi(context, orgId, [tx.id]);
  return { draft, issued: await issueInvoiceApi(context, orgId, draft) };
}

test("an issued invoice has an email form with the customer's address; an attempt is listed and logged", async ({ page, context }) => {
  world = createWorld({ label: "InvoiceEmail" });
  await signIn(context, world.email);
  const { issued } = await issuedInvoice(context, world.orgId);

  await page.goto(`/o/${world.orgId}/invoices/${issued.id}`);
  const panel = page.getByTestId("email-invoice");
  await expect(panel.getByLabel("Send to")).toHaveValue("anna@example.test");
  await expect(panel.getByTestId("no-invoice-emails")).toBeVisible();

  await panel.getByLabel("Message (optional)").fill("Tack för senast!");
  await panel.getByTestId("send-invoice-email").click();
  // no mail service is configured in the test setup: the person is told, and the attempt is kept
  await expect(panel.getByTestId("email-failed")).toContainText("Email sending is not working right now");
  await expect(panel.getByTestId("invoice-email")).toHaveCount(1);
  await expect(panel.getByTestId("invoice-email")).toContainText("Not sent");
  await expect(panel.getByTestId("invoice-email")).toContainText("anna@example.test");

  expect(testRow(`select status || '|' || error || '|' || to_email || '|' || note from invoice_emails where invoice_id = ${sql(issued.id)}`)).toBe("failed|not_configured|anna@example.test|Tack för senast!");

  await page.getByTestId("history-toggle").getByRole("heading", { name: /History/ }).click();
  await expect(page.getByTestId("history-event").filter({ hasText: "Email not sent" })).toHaveCount(1);
});

test("a bad address is explained and a draft invoice has no email form", async ({ page, context }) => {
  world = createWorld({ label: "InvoiceEmailBad" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson", { email: "anna@example.test" });
  const tx = await createCompletedTransaction(context, world.orgId, customer.id);
  const draft = await createInvoiceApi(context, world.orgId, [tx.id]);
  await page.goto(`/o/${world.orgId}/invoices/${draft.id}`);
  await expect(page.getByTestId("email-invoice")).toHaveCount(0);

  const issued = await issueInvoiceApi(context, world.orgId, draft);
  await page.goto(`/o/${world.orgId}/invoices/${issued.id}`);
  await page.getByLabel("Send to").fill("not an address");
  await page.getByTestId("send-invoice-email").click();
  await expect(page.getByText("Enter an email address the invoice can be sent to")).toBeVisible();
  expect(testRow(`select count(*) from invoice_emails where invoice_id = ${sql(issued.id)}`)).toBe("0");
});

test("an employee and a viewer see the list but no form; another organization cannot reach the invoice", async ({ page, context }) => {
  world = createWorld({ label: "InvoiceEmailRoles" });
  other = createWorld({ label: "InvoiceEmailOther" });
  await signIn(context, world.email);
  const { issued } = await issuedInvoice(context, world.orgId);

  for (const role of ["employee", "viewer"] as const) {
    await signIn(context, world.addMember(role));
    await page.goto(`/o/${world.orgId}/invoices/${issued.id}`);
    await expect(page.getByTestId("email-invoice")).toBeVisible();
    await expect(page.getByTestId("send-invoice-email")).toHaveCount(0);
  }

  await signIn(context, other.email);
  const forged = await context.request.post(bffUrl(other.orgId, `/invoices/${issued.id}/email`), { data: { to: "evil@example.test" } });
  expect(forged.status()).toBe(404);
  expect((await context.request.get(bffUrl(other.orgId, `/invoices/${issued.id}/emails`))).status()).toBe(404);
});
