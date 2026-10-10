import { expect, test } from "./fixtures";

import { bffUrl, createCustomer, createHorse, createItem, createTransaction, createWorld, lifecycle, openAddLine, pick, signIn, type World } from "./support";

/**
 * A service performed on a horse, from the Add line form to the horse's page and the invoice: for whom, when (in the
 * organization's time zone), by whom and the notes travel with it; the invoice keeps its own copy.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("a service for a horse is added, listed on the horse and kept on the invoice", async ({ page, context }) => {
  world = createWorld({ label: "Services" });
  await signIn(context, world.email);
  await context.request.patch(bffUrl(world.orgId, "/organization"), { data: { timezone: "Europe/Stockholm" } });
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: anna.id });
  await createItem(context, world.orgId, { name: "Massage", type: "service", unit: "session", price_ex_vat: "850.00" });
  await createItem(context, world.orgId, { name: "Liniment", type: "product", unit: "pcs", price_ex_vat: "120.00" });
  const tx = await createTransaction(context, world.orgId, { billing_customer_id: anna.id, transaction_date: "2026-10-03" });

  await page.goto(`/o/${world.orgId}/transactions/${tx.id}`);
  await openAddLine(page);
  await page.getByLabel("Service", { exact: true }).check();
  await pick(page, "item_id", "Massage");
  await pick(page, "subject_id", "Kalle");
  await page.getByLabel("Performed by").selectOption({ label: `owner ${world.name.split(" ").pop()}` });
  await page.getByLabel(/Performed at/).fill("2026-10-03T14:00");
  await page.getByLabel("Quantity").fill("1");
  await page.getByLabel("Notes").fill("Stiff left shoulder");
  await page.getByTestId("submit-line").click();

  const summary = page.getByTestId("service-summary");
  await expect(summary).toContainText("Service for Kalle · 2026-10-03 14:00 · by owner");
  await expect(page.getByTestId("service-notes")).toHaveText("Stiff left shoulder");

  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  await page.getByRole("heading", { name: /Services performed on this horse/ }).click();
  await expect(page.getByTestId("service-row")).toHaveCount(1);
  await expect(page.getByTestId("service-row")).toContainText("Massage");
  await expect(page.getByTestId("service-row")).toContainText("2026-10-03 14:00");

  await lifecycle(context, world.orgId, tx.id, "complete");
  const invoice = (await (await context.request.post(bffUrl(world.orgId, "/invoices"), { data: { transaction_ids: [tx.id] } })).json()) as { id: string };
  await page.goto(`/o/${world.orgId}/invoices/${invoice.id}`);
  await expect(page.getByTestId("service-summary")).toContainText("Service for Kalle · 2026-10-03 14:00");
});

test("a product cannot be chosen as a service, and a horse with a service cannot be deleted", async ({ context }) => {
  world = createWorld({ label: "ServiceRules" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const horse = await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: anna.id });
  const massage = await createItem(context, world.orgId, { name: "Massage", type: "service", price_ex_vat: "850.00" });
  const liniment = await createItem(context, world.orgId, { name: "Liniment", type: "product", price_ex_vat: "120.00" });
  const tx = await createTransaction(context, world.orgId, { billing_customer_id: anna.id });
  const line = (item: string) =>
    context.request.post(bffUrl(world.orgId, `/transactions/${tx.id}/lines`), {
      data: { kind: "service", item_id: item, quantity: "1", subject_type: "horse", subject_id: horse.id },
    });

  expect((await line(liniment.id)).status()).toBe(422);
  expect((await line(massage.id)).status()).toBe(201);
  expect((await context.request.delete(bffUrl(world.orgId, `/horses/${horse.id}`))).status()).toBe(409);
});
