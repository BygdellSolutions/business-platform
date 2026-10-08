import { expect, test } from "./fixtures";

import { createItem, createWorld, signIn, type World } from "./support";

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
  await expect(panel.getByTestId("on-hand")).toHaveText("0.000");
  await panel.getByLabel(/Counted quantity/).fill("10");
  await panel.getByTestId("submit-stock").click();
  await expect(panel.getByTestId("on-hand")).toHaveText("10.000");

  await panel.getByLabel("Change", { exact: true }).selectOption("remove");
  await panel.getByLabel(/Quantity/).fill("12");
  await panel.getByLabel(/Why/).fill("Damaged");
  await panel.getByTestId("submit-stock").click();
  await expect(panel.getByTestId("error-quantity")).toContainText("below zero");

  await panel.getByLabel(/Quantity/).fill("2");
  await panel.getByTestId("submit-stock").click();
  await expect(panel.getByTestId("on-hand")).toHaveText("8.000");
  await expect(panel.getByTestId("stock-movement")).toHaveCount(2);
  await expect(panel.getByTestId("stock-movement").first()).toContainText("Adjustment");
  await expect(panel.getByTestId("stock-movement").first()).toContainText("Damaged");
  await expect(panel.getByTestId("stock-movement").last()).toContainText("Opening count");
});
