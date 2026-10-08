import { expect, test } from "./fixtures";

import { addLine, bffUrl, createCustomer, createItem, createTransaction, createWorld, ifMatch, signIn, type World } from "./support";

/**
 * Who changed what and when, as a person reads it on the record's page: the author line and the history list,
 * in the organization's own time zone, with names instead of ids.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("a customer shows who created it, who changed it and what it was before", async ({ page, context }) => {
  world = createWorld({ label: "History" });
  await signIn(context, world.email);
  await context.request.patch(bffUrl(world.orgId, "/organization"), { data: { timezone: "Europe/Stockholm" } });
  const customer = await createCustomer(context, world.orgId, "Anna");

  await page.goto(`/o/${world.orgId}/customers/${customer.id}`);
  await expect(page.getByTestId("created-by")).toHaveText(/^owner /);
  await page.getByLabel("Name", { exact: true }).fill("Anna Andersson");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();

  const latest = page.getByTestId("history-event").first();
  await expect(latest).toBeHidden(); // collapsed until asked for
  await page.getByTestId("history-toggle").getByText(/Show history \(2\)/).click();
  await expect(latest).toBeVisible();
  await expect(latest).toHaveAttribute("data-action", "updated");
  await expect(latest.getByTestId("history-change")).toHaveText("Name: Anna → Anna Andersson");
  await expect(page.getByTestId("history-event")).toHaveCount(2);
  await expect(latest).not.toContainText("UTC"); // shown in the organization's zone, not UTC
});

test("a horse's owner change is shown with the customers' names", async ({ page, context }) => {
  world = createWorld({ label: "HorseHistory" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const umea = await createCustomer(context, world.orgId, "Umeå HK");
  const horse = (await (await context.request.post(bffUrl(world.orgId, "/horses"), { data: { name: "Kalle", owner_customer_id: anna.id } })).json()) as { id: string };
  await context.request.patch(bffUrl(world.orgId, `/horses/${horse.id}`), { data: { owner_customer_id: umea.id } });

  await page.goto(`/o/${world.orgId}/horses/${horse.id}`);
  await expect(page.getByTestId("history-event").first().getByTestId("history-change")).toHaveText("Owner: Anna Andersson → Umeå HK");
});

test("a transaction's history includes its lines and its completion", async ({ page, context }) => {
  world = createWorld({ label: "TxHistory" });
  await signIn(context, world.email);
  const customer = await createCustomer(context, world.orgId, "Umeå HK");
  const item = await createItem(context, world.orgId, { name: "Horse massage", price_ex_vat: "850.00" });
  const tx = await createTransaction(context, world.orgId, { billing_customer_id: customer.id });
  await addLine(context, world.orgId, tx.id, { item_id: item.id, quantity: "1" });
  await context.request.post(bffUrl(world.orgId, `/transactions/${tx.id}/complete`), { headers: ifMatch(tx.version + 1) });

  await page.goto(`/o/${world.orgId}/transactions/${tx.id}`);
  const actions = await page.getByTestId("history-event").evaluateAll((items) => items.map((item) => item.getAttribute("data-action")));
  expect(actions).toEqual(["completed", "created", "created"]);
  await expect(page.getByTestId("history-event").nth(1)).toContainText("Line · Created");
  await expect(page.getByTestId("updated-by")).toHaveText(/^owner /);
});
