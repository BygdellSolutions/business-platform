import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import {
  FREDRIK,
  ORG_A,
  addLine,
  bffUrl,
  createCompletedTransaction,
  createCustomer,
  createInvoiceApi,
  createItem,
  createSupplier,
  createTransaction,
  issueInvoiceApi,
  lifecycle,
  organizationToday,
  signIn,
} from "./support";

/**
 * Every block of information sits in a box (`.panel`). Nothing inside a box may stick out past its edge: a wide table
 * scrolls sideways inside the box instead. Checked with realistic data (deliveries with supplier and reference,
 * backorders, payments, stock history) on a laptop and a phone width, with every <details> opened.
 */
async function spills(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    for (const details of document.querySelectorAll("details")) details.open = true;
    const found: string[] = [];
    for (const panel of document.querySelectorAll<HTMLElement>(".panel")) {
      const edge = panel.getBoundingClientRect().right;
      for (const element of panel.querySelectorAll<HTMLElement>("*")) {
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 || rect.right <= edge + 1) continue;
        // Content inside a sideways scroller (within the box) is fine: the scroller keeps it in the box.
        let clipped = false;
        for (let node = element.parentElement; node && node !== panel; node = node.parentElement) {
          if (["auto", "scroll", "hidden"].includes(getComputedStyle(node).overflowX)) clipped = true;
        }
        if (clipped) continue;
        const label = panel.getAttribute("aria-label") ?? panel.dataset.testid ?? panel.tagName;
        found.push(`${label}: <${element.tagName.toLowerCase()}> ends ${Math.round(rect.right - edge)}px past the box`);
        break;
      }
    }
    return found;
  });
}

test("nothing sticks out of its box", async ({ context, page }) => {
  test.setTimeout(120_000);
  await signIn(context, FREDRIK);
  const org = ORG_A.id;
  const today = await organizationToday(context, org);
  const tag = `Fit ${Date.now()}`;
  const supplier = await createSupplier(context, org, `${tag} Nordic Feed and Equestrian Supplies AB`);
  const item = await createItem(context, org, { name: `${tag} Saddle pad deluxe`, type: "product", unit: "pcs", track_stock: true, sku: `FIT-${Date.now()}` });
  const customer = await createCustomer(context, org, `${tag} Customer`);
  expect((await context.request.post(bffUrl(org, `/items/${item.id}/stock`), { data: { kind: "count", quantity: "1" } })).status()).toBe(201);
  expect(
    (
      await context.request.post(bffUrl(org, "/inventory/incoming"), {
        data: { item_id: item.id, quantity: "12", expected_on: today, supplier_id: supplier.id, reference: "PO-2026-000123-LONG-REFERENCE", unit_cost: "149.50" },
      })
    ).status(),
  ).toBe(201);
  expect((await context.request.post(bffUrl(org, `/items/${item.id}/discounts`), { data: { percent: "15", starts_on: today, note: "Autumn campaign" } })).status()).toBe(201);
  // A backorder: 3 ordered, 1 on hand.
  const backordered = await createTransaction(context, org, { billing_customer_id: customer.id, transaction_date: today });
  await addLine(context, org, backordered.id, { item_id: item.id, quantity: "3" });
  await lifecycle(context, org, backordered.id, "complete");
  const sold = await createCompletedTransaction(context, org, customer.id, { date: today });
  const invoice = await issueInvoiceApi(context, org, await createInvoiceApi(context, org, [sold.id]));
  expect(
    (await context.request.post(bffUrl(org, `/invoices/${invoice.id}/payments`), { data: { amount: "100.00", paid_on: today, method: "bank_transfer", reference: "Payment reference 0001" } })).status(),
  ).toBe(201);

  const pages = [
    "",
    "/inventory",
    `/catalog/${item.id}`,
    "/catalog",
    `/customers/${customer.id}`,
    "/customers",
    `/suppliers/${supplier.id}`,
    "/suppliers",
    `/transactions/${backordered.id}`,
    "/transactions",
    `/invoices/${invoice.id}`,
    "/invoices",
    "/horses",
    "/settings",
    "/members",
  ];
  const problems: string[] = [];
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of pages) {
      await page.goto(`/o/${org}${path}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      for (const problem of await spills(page)) problems.push(`${width}px ${path || "/"} ${problem}`);
    }
  }
  expect(problems).toEqual([]);
});

test("the check sees a table that sticks out", async ({ context, page }) => {
  // Control: without it, an observer that never finds anything would pass vacuously.
  await signIn(context, FREDRIK);
  await page.goto(`/o/${ORG_A.id}/inventory`);
  await page.evaluate(() => {
    const panel = document.querySelector(".panel")!;
    const wide = document.createElement("table");
    wide.innerHTML = `<tr><td style="white-space:nowrap">${"wide ".repeat(400)}</td></tr>`;
    panel.append(wide);
  });
  expect((await spills(page)).length).toBeGreaterThan(0);
});
