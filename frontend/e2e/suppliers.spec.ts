import { expect, test } from "./fixtures";

import { createSupplier, createWorld, signIn, type World } from "./support";

/**
 * Suppliers: a register next to Customers. A supplier is created and edited in the browser and persists; another
 * organization's supplier is the same not-found page as a random id.
 */

let world: World;
let other: World;
test.afterEach(() => {
  world?.cleanup();
  other?.cleanup();
});

test("a supplier is created from the Suppliers tab, edited, and found in the list", async ({ page, context }) => {
  world = createWorld({ label: "Suppliers" });
  await signIn(context, world.email);

  await page.goto(`/o/${world.orgId}`);
  await page.getByRole("link", { name: "Suppliers", exact: true }).click();
  await expect(page.getByTestId("empty")).toHaveText("No suppliers yet.");
  await page.getByTestId("new-supplier").click();
  await page.getByLabel("Name").fill("Horse Supplies AB");
  await page.getByLabel("Contact person").fill("Eva Ek");
  await page.getByLabel("Our customer number").fill("K-114");
  await page.getByTestId("submit").click();

  await expect(page.getByTestId("created")).toBeVisible();
  await expect(page.getByTestId("record-name")).toHaveText("Horse Supplies AB");
  await page.getByLabel("Phone").fill("090-123 45");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Phone")).toHaveValue("090-123 45");
  await page.goto(`/o/${world.orgId}/suppliers?q=supplies`);
  await expect(page.getByTestId("supplier-row")).toHaveCount(1);
  await expect(page.getByTestId("supplier-row")).toContainText("Eva Ek");
});

test("another organization's supplier cannot be opened", async ({ page, context }) => {
  world = createWorld({ label: "Suppliers A" });
  other = createWorld({ label: "Suppliers B" });
  await signIn(context, other.email);
  const foreign = await createSupplier(context, other.orgId, "Foreign Supplies");
  await signIn(context, world.email);

  async function outcome(url: string) {
    const response = await page.goto(url);
    await expect(page.getByTestId("not-found")).toBeVisible();
    return { status: response?.status(), text: await page.locator("body").innerText() };
  }
  const foreignOutcome = await outcome(`/o/${world.orgId}/suppliers/${foreign.id}`);
  expect(foreignOutcome).toEqual(await outcome(`/o/${world.orgId}/suppliers/00000000-0000-4000-8000-00000000dead`)); // same as a random id
  expect(foreignOutcome.text).not.toContain("Foreign Supplies");
  await page.goto(`/o/${world.orgId}/suppliers`);
  await expect(page.getByTestId("empty")).toBeVisible();
});
