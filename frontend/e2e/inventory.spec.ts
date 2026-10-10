import { expect, test } from "./fixtures";

import { bffUrl, createCustomer, createItem, createSupplier, createTransaction, createWorld, lifecycle, openAddLine, pick, signIn, type World } from "./support";

/**
 * Inventory I1: a product that tracks stock gets a Stock panel. The first count is the opening stock; every later
 * change needs a reason; the movements explain the on-hand figure; stock never goes below zero.
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("the opening count and later changes are listed and explain the quantity on hand", async ({ page, context }) => {
  world = createWorld({ label: "Inventory" });
  await signIn(context, world.email);
  const item = await createItem(context, world.orgId, { name: "Liniment", type: "product", unit: "pcs", sku: "LIN-01", track_stock: true });

  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  const panel = page.getByTestId("stock-panel");
  await expect(panel.getByTestId("on-hand")).toHaveText("0");
  await panel.getByLabel(/Counted quantity/).fill("10");
  await panel.getByTestId("submit-stock").click();
  await expect(panel.getByTestId("on-hand")).toHaveText("10");

  await panel.getByLabel("Change", { exact: true }).selectOption("remove");
  await panel.getByLabel(/Quantity/).fill("12");
  await panel.getByLabel(/Why/).fill("Damaged");
  await panel.getByTestId("submit-stock").click();
  await expect(panel.getByTestId("error-quantity")).toContainText("below zero");

  await panel.getByLabel(/Quantity/).fill("2");
  await panel.getByTestId("submit-stock").click();
  await expect(panel.getByTestId("on-hand")).toHaveText("8");
  await expect(panel.getByTestId("stock-movement")).toHaveCount(2);
  await expect(panel.getByTestId("stock-movement").first()).toContainText("Adjustment");
  await expect(panel.getByTestId("stock-movement").first()).toContainText("Damaged");
  await expect(panel.getByTestId("stock-movement").last()).toContainText("Opening count");

  await page.goto(`/o/${world.orgId}/catalog`);
  const row = page.getByTestId("item-row").filter({ hasText: "Liniment" });
  await expect(row.getByTestId("item-sku")).toHaveText("LIN-01");
  await expect(row.getByTestId("item-on-hand")).toHaveText("8");
  await expect(row.getByTestId("item-stock-states")).toHaveText("In stock");
});

test("a draft that asks for more than is in stock warns, and the line is still added", async ({ page, context }) => {
  world = createWorld({ label: "Availability" });
  await signIn(context, world.email);
  const item = await createItem(context, world.orgId, { name: "Hoof oil", type: "product", unit: "pcs", price_ex_vat: "90.00", track_stock: true });
  expect((await context.request.post(bffUrl(world.orgId, `/items/${item.id}/stock`), { data: { kind: "count", quantity: "5" } })).status()).toBe(201);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const tx = await createTransaction(context, world.orgId, { billing_customer_id: customer.id });

  await page.goto(`/o/${world.orgId}/transactions/${tx.id}`);
  await openAddLine(page);
  await pick(page, "item_id", "Hoof oil");
  await expect(page.getByTestId("item-availability")).toContainText("In stock: 5, on other drafts: 0, available: 5");
  await page.getByLabel("Quantity").fill("8");
  await page.getByTestId("submit-line").click();

  await expect(page.getByTestId("line-row")).toHaveCount(1);
  await expect(page.getByTestId("stock-warning")).toHaveText("In stock for this line: 5 of 8. 3 will be backordered at completion.");

  // The draft's 8 are allocated: not final, but no longer shown as available to anyone else.
  await page.goto(`/o/${world.orgId}/inventory`);
  await expect(page.getByTestId("stock-row-allocated")).toHaveText("8");
  await expect(page.getByTestId("stock-row-available")).toHaveText("0");
});

test("completion delivers what is in stock and backorders the rest; a reopen gives it back", async ({ page, context }) => {
  world = createWorld({ label: "Fulfillment" });
  await signIn(context, world.email);
  const item = await createItem(context, world.orgId, { name: "Hoof oil", type: "product", unit: "pcs", price_ex_vat: "90.00", track_stock: true });
  expect((await context.request.post(bffUrl(world.orgId, `/items/${item.id}/stock`), { data: { kind: "count", quantity: "5" } })).status()).toBe(201);
  const customer = await createCustomer(context, world.orgId, "Anna Andersson");
  const tx = await createTransaction(context, world.orgId, { billing_customer_id: customer.id });
  expect((await context.request.post(bffUrl(world.orgId, `/transactions/${tx.id}/lines`), { data: { item_id: item.id, quantity: "8" } })).status()).toBe(201);
  await lifecycle(context, world.orgId, tx.id, "complete");

  await page.goto(`/o/${world.orgId}/transactions/${tx.id}`);
  await expect(page.getByTestId("line-fulfillment")).toHaveText("Delivered 5 · Backordered 3 (waiting for stock)");
  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  await expect(page.getByTestId("on-hand")).toHaveText("0");
  await expect(page.getByTestId("stock-movement").first()).toContainText("Delivered");

  await lifecycle(context, world.orgId, tx.id, "reopen");
  await page.reload();
  await expect(page.getByTestId("on-hand")).toHaveText("5");
  await expect(page.getByTestId("stock-movement").first()).toContainText("Returned");
  await expect(page.getByTestId("stock-movement").first()).toContainText("Order reopened");
});

test("a delivery on its way is recorded, received in part, and the rest cancelled", async ({ page, context }) => {
  world = createWorld({ label: "Incoming" });
  await signIn(context, world.email);
  const item = await createItem(context, world.orgId, { name: "Fly spray", type: "product", unit: "pcs", price_ex_vat: "150.00", track_stock: true });
  const supplier = await createSupplier(context, world.orgId, "Horse Supplies AB");

  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  const incoming = page.getByTestId("incoming-panel");
  await expect(incoming.getByTestId("no-incoming")).toBeVisible();
  await incoming.getByLabel(/^Quantity/).fill("10");
  await pick(page, "supplier_id", supplier.name); // chosen from the register, never typed
  await incoming.getByLabel("Reference (optional)").fill("PO-17");
  await incoming.getByLabel("Unit cost excl. VAT (optional)").fill("61.50");
  await incoming.getByTestId("submit-incoming").click();
  await expect(incoming.getByTestId("incoming-row")).toContainText("Expected");
  await expect(incoming.getByTestId("incoming-unit-cost")).toHaveText("61.50");
  await expect(incoming.getByTestId("incoming-ordered-on")).toHaveText(/^\d{4}-\d{2}-\d{2}$/);

  // The supplier moved the date and the invoice said another price: both change on the row.
  await incoming.getByTestId("edit-incoming").click();
  const edit = incoming.getByTestId("incoming-edit");
  await edit.getByLabel("Expected on").fill("2026-12-24");
  await edit.getByLabel("Unit cost excl. VAT").fill("63");
  await incoming.getByTestId("save-incoming").click();
  await expect(incoming.getByTestId("incoming-expected")).toHaveText("2026-12-24");
  await expect(incoming.getByTestId("incoming-unit-cost")).toHaveText("63.00");
  await expect(incoming.getByTestId("supplier-link")).toHaveText("Horse Supplies AB");
  await incoming.getByTestId("supplier-link").click();
  await page.getByRole("heading", { name: "Deliveries from this supplier (1)" }).click();
  await expect(page.getByTestId("supplier-delivery")).toContainText("Fly spray");
  await expect(page.getByTestId("delivery-unit-cost")).toHaveText("63.00");
  await expect(page.getByTestId("delivery-ordered-on")).toHaveText(/^\d{4}-\d{2}-\d{2}$/);
  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  await expect(page.getByTestId("stock-figures")).toContainText("incoming 10");
  await expect(page.getByTestId("on-hand")).toHaveText("0");

  await incoming.getByLabel("Quantity received").fill("4");
  await incoming.getByTestId("receive-incoming").click();
  await expect(incoming.getByTestId("incoming-row")).toContainText("Partly received");
  await expect(incoming.getByTestId("incoming-remaining")).toHaveText("6");
  await page.goto(`/o/${world.orgId}/inventory`);
  await expect(page.getByTestId("incoming-overview-ordered")).toHaveText("10");
  await expect(page.getByTestId("incoming-overview-received")).toHaveText("4");
  await expect(page.getByTestId("incoming-overview-remaining")).toHaveText("6");
  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  await expect(page.getByTestId("on-hand")).toHaveText("4");
  await expect(page.getByTestId("stock-movement").first()).toContainText("Goods received");

  await incoming.getByTestId("cancel-incoming").click();
  await page.getByRole("button", { name: "Yes, cancel the rest" }).click();
  // It stays listed (what was paid is history), with no receive or cancel any more.
  await expect(incoming.getByTestId("incoming-row")).toContainText("Cancelled");
  await expect(incoming.getByTestId("receive-incoming")).toHaveCount(0);
  await expect(page.getByTestId("on-hand")).toHaveText("4");
  await expect(page.getByTestId("stock-figures")).toContainText("incoming 0");
});

test("received stock goes to waiting sales only when a person confirms the oldest-first proposal", async ({ page, context }) => {
  world = createWorld({ label: "Backlog" });
  await signIn(context, world.email);
  const item = await createItem(context, world.orgId, { name: "Hoof oil", type: "product", unit: "pcs", price_ex_vat: "90.00", track_stock: true });
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const club = await createCustomer(context, world.orgId, "Umeå HK");
  for (const [customer, quantity] of [[anna, "2"], [club, "3"]] as const) {
    const tx = await createTransaction(context, world.orgId, { billing_customer_id: customer.id });
    expect((await context.request.post(bffUrl(world.orgId, `/transactions/${tx.id}/lines`), { data: { item_id: item.id, quantity } })).status()).toBe(201);
    await lifecycle(context, world.orgId, tx.id, "complete");
  }
  const incoming = (await (await context.request.post(bffUrl(world.orgId, "/inventory/incoming"), { data: { item_id: item.id, quantity: "4" } })).json()) as { id: string };
  expect((await context.request.post(bffUrl(world.orgId, `/inventory/incoming/${incoming.id}/receive`), { data: {} })).status()).toBe(200);

  await page.goto(`/o/${world.orgId}/inventory`);
  await expect(page.getByTestId("backlog-row")).toHaveCount(2);
  await expect(page.getByTestId("backlog-row").first()).toContainText("Ready to fulfill"); // the state label renders on the server page
  await expect(page.getByTestId("stock-row")).toHaveCount(1); // the product, with what is on hand and what is promised
  await expect(page.getByTestId("stock-row-on-hand")).toHaveText("4");
  await expect(page.getByTestId("stock-row")).toContainText("Backordered");

  await page.goto(`/o/${world.orgId}/catalog/${item.id}`);
  const panel = page.getByTestId("backorders-panel");
  await expect(panel.getByTestId("backorder-state")).toHaveText(["Ready to fulfill", "Ready to fulfill"]);
  await panel.getByTestId("propose-allocation").click();
  await expect(panel.getByTestId("allocation-proposed")).toContainText("from 4 on hand");
  await expect(panel.getByTestId("allocation-quantity").first()).toHaveValue("2");
  await expect(panel.getByTestId("allocation-quantity").last()).toHaveValue("2");
  await panel.getByTestId("confirm-allocation").click();

  await expect(panel.getByTestId("backorder-row")).toHaveCount(1); // Anna's sale is fulfilled; Umeå HK still waits for 1
  await expect(panel.getByTestId("backorder-row")).toContainText("Umeå HK");
  await expect(panel.getByTestId("backorder-waiting")).toHaveText("1");
  await expect(page.getByTestId("on-hand")).toHaveText("0");
});

test("a new product can be created with what is on hand, and a product without tracking says how to turn it on", async ({ page, context }) => {
  world = createWorld({ label: "OpeningStock" });
  await signIn(context, world.email);

  await page.goto(`/o/${world.orgId}/catalog/new`);
  await page.getByLabel("Type").selectOption("product");
  await page.getByLabel("Name", { exact: true }).fill("Hoof oil");
  await page.getByLabel("Unit", { exact: true }).fill("pcs");
  await page.getByLabel("Price excluding VAT").fill("90");
  await page.getByLabel("VAT rate (%)").fill("25");
  await page.getByLabel("Track stock").check();
  await page.getByLabel("On hand now (optional)").fill("12");
  await page.getByTestId("submit").click();

  await expect(page.getByTestId("created")).toBeVisible();
  await expect(page.getByTestId("on-hand")).toHaveText("12");
  await expect(page.getByTestId("stock-movement")).toContainText("Opening count");

  const plain = await createItem(context, world.orgId, { name: "Plain product", type: "product" });
  await page.goto(`/o/${world.orgId}/catalog/${plain.id}`);
  await expect(page.getByTestId("stock-off")).toContainText("Track stock");
  await page.getByLabel("Track stock").check();
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("stock-panel")).toBeVisible();
});
