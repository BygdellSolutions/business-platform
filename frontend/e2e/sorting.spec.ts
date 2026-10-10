import { expect, test } from "./fixtures";

import { bffUrl, createCustomer, createItem, createSupplier, createWorld, signIn, type World } from "./support";

/** Every table sorts by a column (2026-10-10): paged lists on the server, the other tables in place. */
let world: World;
test.afterEach(() => world?.cleanup());

const cells = (page: import("@playwright/test").Page, row: string, nth: number) => page.getByTestId(row).locator(`td:nth-child(${nth})`).allTextContents();

test("a heading sorts its table, a second click turns the order around, and paging and filtering keep it", async ({ page, context }) => {
  world = createWorld({ label: "Sorting" });
  await signIn(context, world.email);
  for (const name of ["Bo", "anna", "Cia"]) await createCustomer(context, world.orgId, name);
  await createItem(context, world.orgId, { name: "Cheap", price_ex_vat: "10.00" });
  await createItem(context, world.orgId, { name: "Dear", price_ex_vat: "900.00" });
  await createItem(context, world.orgId, { name: "Middle", price_ex_vat: "95.50" });

  // A paged list: the backend sorts, the address says so.
  await page.goto(`/o/${world.orgId}/customers`);
  await page.getByTestId("sort-name").click();
  await expect(page).toHaveURL(/sort=name/);
  await expect.poll(() => cells(page, "customer-row", 2)).toEqual(["anna", "Bo", "Cia"]);
  await page.getByTestId("sort-name").click();
  await expect(page).toHaveURL(/dir=desc/);
  await expect.poll(() => cells(page, "customer-row", 2)).toEqual(["Cia", "Bo", "anna"]);
  await expect(page.getByRole("columnheader", { name: /Name/ })).toHaveAttribute("aria-sort", "descending");
  await page.getByRole("search").getByLabel("Search").fill("a");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/sort=name.*dir=desc|dir=desc.*sort=name/); // filtering keeps the order
  await expect.poll(() => cells(page, "customer-row", 2)).toEqual(["Cia", "anna"]);

  // Prices are decimal strings: 95.50 sorts between 10.00 and 900.00 (not as text).
  await page.goto(`/o/${world.orgId}/catalog`);
  await page.getByTestId("sort-price").click();
  await expect.poll(() => cells(page, "item-row", 2)).toEqual(["Cheap", "Middle", "Dear"]);
});

test("a table on a record's page sorts in place", async ({ page, context }) => {
  world = createWorld({ label: "Sorting" });
  await signIn(context, world.email);
  const supplier = await createSupplier(context, world.orgId, "Horse Supplies AB");
  for (const [name, cost] of [["Spray", "120.00"], ["Oil", "9.90"], ["Shampoo", "45.00"]] as const) {
    const item = await createItem(context, world.orgId, { name, type: "product", unit: "pcs", price_ex_vat: "1.00", track_stock: true });
    const response = await context.request.post(bffUrl(world.orgId, "/inventory/incoming"), {
      data: { item_id: item.id, quantity: "1", supplier_id: supplier.id, unit_cost: cost },
    });
    expect(response.status(), await response.text()).toBe(201);
  }
  await page.goto(`/o/${world.orgId}/suppliers/${supplier.id}`);
  await page.getByRole("heading", { name: /Deliveries from this supplier/ }).click();
  await page.getByTestId("sort-unit_cost").click();
  await expect.poll(() => page.getByTestId("delivery-unit-cost").allTextContents()).toEqual(["9.90", "45.00", "120.00"]);
  await page.getByTestId("sort-unit_cost").click();
  await expect.poll(() => page.getByTestId("delivery-unit-cost").allTextContents()).toEqual(["120.00", "45.00", "9.90"]);
});
