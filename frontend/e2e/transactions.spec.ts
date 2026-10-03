import { expect, test, type Page } from "./fixtures";

import { BACKEND_URL } from "./env";
import {
  addLine,
  bffUrl,
  browserToday,
  createCustomer,
  createItem,
  createTransaction,
  FREDRIK,
  getTransaction,
  ifMatch,
  lifecycle,
  openAddLine,
  ORG_A,
  pick,
  picker,
  setActive,
  signIn,
  sql,
  testRow,
  unique,
  withRequiredTransactionField,
} from "./support";

/**
 * The Transactions workflows in a real browser against the real stack and the dedicated test
 * database. Totals and line amounts are never computed in these tests: they are compared with
 * what PostgreSQL stored (SQL SUM / ::text) and with literals whose arithmetic is obvious.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const list = `/o/${ORG_A.id}/transactions`;
const rows = (page: Page) => page.getByTestId("line-row");
const rowFor = (page: Page, text: string) => rows(page).filter({ hasText: text });
const cell = (row: ReturnType<typeof rowFor>, id: string) => row.getByTestId(id);
const quantity = (page: Page) => page.getByLabel("Quantity", { exact: true });

async function newDraft(context: Parameters<typeof createCustomer>[0], label = "Billing") {
  const customer = await createCustomer(context, ORG_A.id, unique(label));
  const transaction = await createTransaction(context, ORG_A.id, { billing_customer_id: customer.id, transaction_date: "2026-10-01" });
  return { customer, transaction, url: `${list}/${transaction.id}` };
}

async function addCatalogLine(page: Page, itemName: string, qty: string) {
  await openAddLine(page);
  await pick(page, "item_id", itemName);
  await quantity(page).fill(qty);
  await page.getByTestId("submit-line").click();
  await expect(page.getByTestId("add-line-form")).toHaveCount(0);
}

async function addAdHoc(page: Page, values: { description: string; unit: string; quantity: string; price: string; vat: string }) {
  await openAddLine(page);
  await page.getByLabel("Ad-hoc line").check();
  await page.getByLabel("Description", { exact: true }).fill(values.description);
  await page.getByLabel("Unit", { exact: true }).fill(values.unit);
  await quantity(page).fill(values.quantity);
  await page.getByLabel("Unit price excluding VAT", { exact: true }).fill(values.price);
  await page.getByLabel("VAT rate (%)", { exact: true }).fill(values.vat);
  await page.getByTestId("submit-line").click();
  await expect(page.getByTestId("add-line-form")).toHaveCount(0);
}

test.describe("creating a transaction", () => {
  test("a draft is created from a customer and a date, and opens on its own page", async ({ page, context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("Create Billing"));

    await page.goto(`${list}/new`);
    const today = await browserToday(page);
    await expect(page.getByLabel("Date")).toHaveValue(today); // the browser's local date, prefilled
    await pick(page, "billing_customer_id", customer.name);
    await page.getByTestId("submit").click();

    await expect(page).toHaveURL(new RegExp(`${list}/[0-9a-f-]{36}\\?created=1$`));
    await expect(page.getByTestId("created")).toBeVisible();
    await expect(page.getByTestId("tx-status")).toHaveText("Draft");
    await expect(page.getByTestId("header-customer")).toContainText(customer.name);
    await expect(page.getByTestId("header-date")).toHaveText(today);
    await expect(page.getByTestId("no-lines")).toBeVisible();
    await expect(page.getByTestId("total-gross")).toHaveText("0.00");

    const id = page.url().match(/transactions\/([0-9a-f-]{36})/)![1];
    expect(testRow(`select organization_id || '|' || billing_customer_id || '|' || transaction_date::text || '|' || status || '|' || version || '|' || header_version from transactions where id = ${sql(id)}`)).toBe(
      `${ORG_A.id}|${customer.id}|${today}|draft|1|1`,
    );
    expect(testRow(`select count(*) from transaction_lines where transaction_id = ${sql(id)}`)).toBe("0");
  });

  test("the date can be chosen, and goes over the wire as a plain YYYY-MM-DD string", async ({ page, context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("Dated Billing"));
    await page.goto(`${list}/new`);
    await pick(page, "billing_customer_id", customer.name);
    await page.getByLabel("Date").fill("2026-12-24");

    const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/api/o/${ORG_A.id}/transactions`));
    await page.getByTestId("submit").click();
    const sent = (await request).postData() ?? "";

    expect(JSON.parse(sent)).toEqual({ billing_customer_id: customer.id, transaction_date: "2026-12-24" });
    await expect(page.getByTestId("header-date")).toHaveText("2026-12-24");
  });

  test("only active customers can be billed: an inactive one is not offered", async ({ page, context }) => {
    const prefix = unique("Billable");
    const active = await createCustomer(context, ORG_A.id, `${prefix} Active`);
    const inactive = await createCustomer(context, ORG_A.id, `${prefix} Inactive`);
    await setActive(context, ORG_A.id, "customers", inactive.id, false);

    await page.goto(`${list}/new`);
    await picker(page, "billing_customer_id").getByRole("combobox").fill(prefix);
    await expect(picker(page, "billing_customer_id").getByRole("option")).toHaveCount(1);
    await expect(picker(page, "billing_customer_id").getByRole("option")).toContainText(active.name);
  });

  test("with no customer the backend's answer is shown on the customer control and nothing is created", async ({ page }) => {
    const before = testRow(`select count(*) from transactions where organization_id = ${sql(ORG_A.id)}`);
    await page.goto(`${list}/new`);
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-billing_customer_id")).toBeVisible();
    await expect(picker(page, "billing_customer_id").getByRole("combobox")).toHaveAttribute("aria-invalid", "true");
    expect(testRow(`select count(*) from transactions where organization_id = ${sql(ORG_A.id)}`)).toBe(before);
  });

  test("a customer who became inactive after being chosen is refused on the customer control", async ({ page, context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("Race Billing"));
    await page.goto(`${list}/new`);
    await pick(page, "billing_customer_id", customer.name);
    await setActive(context, ORG_A.id, "customers", customer.id, false);

    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-billing_customer_id")).toContainText("inactive");
    await expect(page).toHaveURL(`${list}/new`);
  });

  test("browser Back to a list visited before the create shows the new transaction", async ({ page, context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("Back Billing"));
    await page.goto(`${list}?billing_customer_id=${customer.id}`);
    await expect(page.getByTestId("empty")).toBeVisible();

    await page.goto(`${list}/new`);
    await pick(page, "billing_customer_id", customer.name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();
    await page.goBack();
    await page.goBack();

    await expect(page).toHaveURL(`${list}?billing_customer_id=${customer.id}`);
    await expect(page.getByTestId("transaction-row")).toHaveCount(1);
  });
});

test.describe("lines from the catalog", () => {
  test("only the item and the quantity are sent; the server's snapshot is what appears", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    const item = await createItem(context, ORG_A.id, { name: unique("Snap Item"), unit: "hour", price_ex_vat: "100.00", vat_rate: "25" });

    await page.goto(url);
    await openAddLine(page);
    await pick(page, "item_id", item.name);
    await quantity(page).fill("2.5");
    const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/transactions/${transaction.id}/lines`));
    await page.getByTestId("submit-line").click();

    expect(JSON.parse((await request).postData() ?? "")).toEqual({ item_id: item.id, quantity: "2.5" }); // nothing else
    const row = rowFor(page, item.name);
    await expect(row).toHaveCount(1);
    await expect(cell(row, "line-unit")).toHaveText("hour");
    await expect(cell(row, "line-quantity")).toHaveText("2.500");
    await expect(cell(row, "line-price")).toHaveText("100.00");
    await expect(cell(row, "line-vat-rate")).toHaveText("25.00");
    await expect(cell(row, "line-net")).toHaveText("250.00");
    await expect(cell(row, "line-vat")).toHaveText("62.50");
    await expect(cell(row, "line-gross")).toHaveText("312.50");
    await expect(row.getByRole("link", { name: "Catalog item" })).toHaveAttribute("href", `/o/${ORG_A.id}/catalog/${item.id}`);
    expect(testRow(`select item_id || '|' || description || '|' || unit || '|' || quantity::text || '|' || unit_price_ex_vat::text || '|' || vat_rate::text || '|' || version from transaction_lines where transaction_id = ${sql(transaction.id)}`)).toBe(
      `${item.id}|${item.name}|hour|2.500|100.00|25.00|1`,
    );
  });

  test("an existing line keeps its snapshot when the item is renamed, repriced or deactivated; a new line gets the new values", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    const name = unique("Moving Item");
    const item = await createItem(context, ORG_A.id, { name, unit: "hour", price_ex_vat: "100.00", vat_rate: "25" });
    await page.goto(url);
    await addCatalogLine(page, name, "1");
    await expect(rowFor(page, name)).toHaveCount(1);

    // The catalog changes afterwards.
    const renamed = `${name} renamed`;
    const edit = await context.request.patch(bffUrl(ORG_A.id, `/items/${item.id}`), { data: { name: renamed, price_ex_vat: "200.00", vat_rate: "6", unit: "visit" } });
    expect(edit.status()).toBe(200);
    await page.reload();

    const old = rows(page).first();
    await expect(cell(old, "line-description")).toHaveText(name); // not the new name
    await expect(cell(old, "line-unit")).toHaveText("hour");
    await expect(cell(old, "line-price")).toHaveText("100.00");
    await expect(cell(old, "line-vat-rate")).toHaveText("25.00");

    // A line added now takes the item's CURRENT values, and the first line is still untouched.
    await addCatalogLine(page, renamed, "1");
    await expect(rows(page)).toHaveCount(2);
    const fresh = rows(page).nth(1);
    await expect(cell(fresh, "line-description")).toHaveText(renamed);
    await expect(cell(fresh, "line-price")).toHaveText("200.00");
    await expect(cell(fresh, "line-vat-rate")).toHaveText("6.00");
    await expect(cell(rows(page).first(), "line-price")).toHaveText("100.00");

    // Deactivating the item changes nothing already on the transaction, and it is no longer offered.
    await setActive(context, ORG_A.id, "items", item.id, false);
    await page.reload();
    await expect(rows(page)).toHaveCount(2);
    await expect(cell(rows(page).first(), "line-description")).toHaveText(name);
    await openAddLine(page);
    await picker(page, "item_id").getByRole("combobox").fill(renamed);
    await expect(picker(page, "item_id").getByText("No matches")).toBeVisible();
    expect((await getTransaction(context, ORG_A.id, transaction.id)).lines.map((l) => l.unit_price_ex_vat)).toEqual(["100.00", "200.00"]);
  });

  test("an item deactivated after it was chosen is refused on the Item control, and no line is added", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    const item = await createItem(context, ORG_A.id, { name: unique("Race Item") });
    await page.goto(url);
    await openAddLine(page);
    await pick(page, "item_id", item.name);
    await quantity(page).fill("1");
    await setActive(context, ORG_A.id, "items", item.id, false);

    await page.getByTestId("submit-line").click();

    await expect(page.getByTestId("error-item_id")).toContainText("inactive");
    await expect(page.getByTestId("add-line-form")).toBeVisible(); // the draft stays
    expect(testRow(`select count(*) from transaction_lines where transaction_id = ${sql(transaction.id)}`)).toBe("0");
  });

  test("without an item, or with a quantity that is not a number, nothing is sent", async ({ page, context }) => {
    const { url } = await newDraft(context);
    const item = await createItem(context, ORG_A.id, { name: unique("Local Item") });
    const posts: string[] = [];
    page.on("request", (r) => r.method() === "POST" && posts.push(r.url()));
    await page.goto(url);
    await openAddLine(page);
    await quantity(page).fill("1");
    await page.getByTestId("submit-line").click();
    await expect(page.getByTestId("error-item_id")).toHaveText("Choose an item.");

    await pick(page, "item_id", item.name);
    for (const typed of ["", "abc", "1,5", "-2"]) {
      await quantity(page).fill(typed);
      await page.getByTestId("submit-line").click();
      await expect(page.getByTestId("error-quantity")).toContainText("Enter a number");
    }
    expect(posts).toEqual([]);
  });

  test("a quantity the backend refuses is shown on the quantity control", async ({ page, context }) => {
    const { url } = await newDraft(context);
    const item = await createItem(context, ORG_A.id, { name: unique("Zero Item") });
    await page.goto(url);
    await openAddLine(page);
    await pick(page, "item_id", item.name);
    await quantity(page).fill("0");
    await page.getByTestId("submit-line").click();
    await expect(page.getByTestId("error-quantity")).toBeVisible();
    await quantity(page).fill("9999999999");
    await page.getByTestId("submit-line").click();
    await expect(page.getByTestId("error-quantity")).toBeVisible();
    await expect(rows(page)).toHaveCount(0);
  });
});

test.describe("ad-hoc lines and exact decimals", () => {
  test("an ad-hoc line is stored with no item and shown as Ad-hoc", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await page.goto(url);

    await addAdHoc(page, { description: "Travel", unit: "km", quantity: "12.5", price: "3.50", vat: "6" });

    const row = rows(page).first();
    await expect(cell(row, "line-description")).toHaveText("Travel");
    await expect(cell(row, "line-item")).toHaveText("Ad-hoc");
    await expect(cell(row, "line-net")).toHaveText("43.75");
    expect(testRow(`select (item_id is null)::text || '|' || description || '|' || quantity::text || '|' || unit_price_ex_vat::text from transaction_lines where transaction_id = ${sql(transaction.id)}`)).toBe("true|Travel|12.500|3.50");
  });

  for (const value of [
    { price: "0.10", vat: "8.20", qty: "2.375" },
    { price: "4.35", vat: "0.10", qty: "0.001" },
    { price: "8.20", vat: "100", qty: "1" },
    { price: "9999999999.99", vat: "0", qty: "1" },
  ]) {
    test(`price ${value.price}, VAT ${value.vat}, quantity ${value.qty}: browser → BFF → FastAPI → PostgreSQL → screen with no drift`, async ({ page, context }) => {
      const { transaction, url } = await newDraft(context);
      await page.goto(url);
      await openAddLine(page);
      await page.getByLabel("Ad-hoc line").check();
      await page.getByLabel("Description", { exact: true }).fill("Exact");
      await page.getByLabel("Unit", { exact: true }).fill("u");
      await quantity(page).fill(value.qty);
      await page.getByLabel("Unit price excluding VAT", { exact: true }).fill(value.price);
      await page.getByLabel("VAT rate (%)", { exact: true }).fill(value.vat);
      const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/transactions/${transaction.id}/lines`));
      const response = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith(`/transactions/${transaction.id}/lines`));
      await page.getByTestId("submit-line").click();

      // 1. quoted strings go out exactly as typed
      const sent = (await request).postData() ?? "";
      expect(sent).toContain(`"quantity":"${value.qty}"`);
      expect(sent).toContain(`"unit_price_ex_vat":"${value.price}"`);
      expect(sent).toContain(`"vat_rate":"${value.vat}"`);
      expect(sent).not.toMatch(/"(quantity|unit_price_ex_vat|vat_rate)":\s*[0-9]/);
      // 2. the answer is strings (taken from the BFF's raw text)
      const answer = await (await response).text();
      expect(answer).toMatch(/"unit_price_ex_vat":\s*"[0-9.]+"/);
      expect(answer).toMatch(/"net_amount":\s*"[0-9.]+"/);

      // 3. PostgreSQL's stored text is what the screen prints, cell by cell
      const stored = testRow(
        `select quantity::text || '|' || unit_price_ex_vat::text || '|' || vat_rate::text || '|' || net_amount::text || '|' || vat_amount::text || '|' || gross_amount::text from transaction_lines where transaction_id = ${sql(transaction.id)}`,
      ).split("|");
      const row = rows(page).first();
      await expect(cell(row, "line-quantity")).toHaveText(stored[0]);
      await expect(cell(row, "line-price")).toHaveText(stored[1]);
      await expect(cell(row, "line-vat-rate")).toHaveText(stored[2]);
      await expect(cell(row, "line-net")).toHaveText(stored[3]);
      await expect(cell(row, "line-vat")).toHaveText(stored[4]);
      await expect(cell(row, "line-gross")).toHaveText(stored[5]);
      expect(stored[1]).toBe(value.price.includes(".") ? value.price : `${value.price}.00`);

      // 4. FastAPI itself (asked directly) says the same, in strings
      const direct = await context.request.get(`${BACKEND_URL}/api/transactions/${transaction.id}`, { headers: { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id } });
      const directText = await direct.text();
      expect(directText).toMatch(new RegExp(`"unit_price_ex_vat":\\s*"${stored[1]}"`));
      expect(directText).toMatch(new RegExp(`"gross_amount":\\s*"${stored[5]}"`));

      // 5. and after a reload, from the server again
      await page.reload();
      await expect(cell(rows(page).first(), "line-gross")).toHaveText(stored[5]);
    });
  }

  test("an ad-hoc line the backend refuses is shown on its controls, and the draft stays", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await page.goto(url);
    await openAddLine(page);
    await page.getByLabel("Ad-hoc line").check();
    await quantity(page).fill("1");
    await page.getByLabel("Unit price excluding VAT", { exact: true }).fill("10");
    await page.getByLabel("VAT rate (%)", { exact: true }).fill("101"); // above 100; description and unit left empty

    await page.getByTestId("submit-line").click();

    await expect(page.getByTestId("error-vat_rate")).toBeVisible();
    await expect(page.getByTestId("error-description")).toBeVisible();
    await expect(page.getByTestId("error-unit")).toBeVisible();
    await expect(page.getByLabel("VAT rate (%)", { exact: true })).toHaveValue("101");
    expect(testRow(`select count(*) from transaction_lines where transaction_id = ${sql(transaction.id)}`)).toBe("0");
  });
});

test.describe("totals belong to the server", () => {
  test("totals and the VAT breakdown equal what PostgreSQL sums, on the page and in the list", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await page.goto(url);
    await addAdHoc(page, { description: "A", unit: "u", quantity: "2.375", price: "0.10", vat: "8.20" });
    await addAdHoc(page, { description: "B", unit: "u", quantity: "3", price: "19.99", vat: "25" });
    await addAdHoc(page, { description: "C", unit: "u", quantity: "1.5", price: "7.35", vat: "25" });
    await addAdHoc(page, { description: "D", unit: "u", quantity: "1", price: "4.35", vat: "6" });
    await expect(rows(page)).toHaveCount(4);

    const sums = testRow(`select sum(net_amount)::text || '|' || sum(vat_amount)::text || '|' || sum(gross_amount)::text from transaction_lines where transaction_id = ${sql(transaction.id)}`).split("|");
    await expect(page.getByTestId("total-net")).toHaveText(sums[0]);
    await expect(page.getByTestId("total-vat")).toHaveText(sums[1]);
    await expect(page.getByTestId("total-gross")).toHaveText(sums[2]);

    const breakdown = testRow(`select string_agg(vat_rate::text || ':' || sum_net || ':' || sum_vat, ',' order by vat_rate) from (select vat_rate, sum(net_amount)::text as sum_net, sum(vat_amount)::text as sum_vat from transaction_lines where transaction_id = ${sql(transaction.id)} group by vat_rate) s`);
    const shown = await page.getByTestId("vat-row").evaluateAll((trs) => trs.map((tr) => Array.from(tr.querySelectorAll("td")).map((td) => td.textContent).join(":")));
    expect(shown.join(",")).toBe(breakdown);

    await page.goto(`${list}?billing_customer_id=${transaction.billing_customer_id}`);
    await expect(page.getByTestId("transaction-net")).toHaveText(sums[0]);
    await expect(page.getByTestId("transaction-vat")).toHaveText(sums[1]);
    await expect(page.getByTestId("transaction-gross")).toHaveText(sums[2]);
  });

  test("the totals are re-read from the server after every line change", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await addLine(context, ORG_A.id, transaction.id, { description: "First", unit: "u", quantity: "1", unit_price_ex_vat: "100.00", vat_rate: "25" });
    await addLine(context, ORG_A.id, transaction.id, { description: "Second", unit: "u", quantity: "1", unit_price_ex_vat: "50.00", vat_rate: "25" });
    await page.goto(url);
    await expect(page.getByTestId("total-net")).toHaveText("150.00");
    await expect(page.getByTestId("total-gross")).toHaveText("187.50");

    await rowFor(page, "First").getByTestId("edit-line").click();
    await quantity(page).fill("3");
    await page.getByTestId("save-line").click();
    await expect(page.getByTestId("total-net")).toHaveText("350.00");
    await expect(page.getByTestId("total-gross")).toHaveText("437.50");

    await rowFor(page, "Second").getByTestId("delete-line").click();
    await rowFor(page, "Second").getByTestId("delete-line-confirm").click();
    await expect(rows(page)).toHaveCount(1);
    await expect(page.getByTestId("total-net")).toHaveText("300.00");
    expect(testRow(`select sum(net_amount)::text from transaction_lines where transaction_id = ${sql(transaction.id)}`)).toBe("300.00");
  });

  test("Back to the list after changing lines shows the new totals", async ({ page, context }) => {
    const { transaction } = await newDraft(context);
    await page.goto(`${list}?billing_customer_id=${transaction.billing_customer_id}`);
    await expect(page.getByTestId("transaction-gross")).toHaveText("0.00");

    await page.getByTestId("transaction-link").click();
    await addAdHoc(page, { description: "Later", unit: "u", quantity: "1", price: "80.00", vat: "25" });
    await expect(page.getByTestId("total-gross")).toHaveText("100.00");
    await page.goBack();

    await expect(page.getByTestId("transaction-gross")).toHaveText("100.00");
  });
});

test.describe("editing lines", () => {
  test("an override changes the line only: the catalog item is untouched, and no item id is sent", async ({ page, context }) => {
    const { url } = await newDraft(context);
    const item = await createItem(context, ORG_A.id, { name: unique("Override Item"), unit: "hour", price_ex_vat: "100.00", vat_rate: "25", description: "Catalog text" });
    const itemBefore = testRow(`select name || '|' || unit || '|' || price_ex_vat::text || '|' || vat_rate::text || '|' || description || '|' || updated_at::text from items where id = ${sql(item.id)}`);
    await page.goto(url);
    await addCatalogLine(page, item.name, "1");

    await rows(page).first().getByTestId("edit-line").click();
    await page.getByLabel("Description", { exact: true }).fill("Custom wording");
    await page.getByLabel("Unit price excluding VAT", { exact: true }).fill("150.00");
    await page.getByLabel("VAT rate (%)", { exact: true }).fill("6");
    const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/lines/"));
    await page.getByTestId("save-line").click();

    const sent = JSON.parse((await request).postData() ?? "");
    expect(sent).toEqual({ description: "Custom wording", unit_price_ex_vat: "150.00", vat_rate: "6" });
    expect("item_id" in sent).toBe(false);
    const row = rows(page).first();
    await expect(cell(row, "line-description")).toHaveText("Custom wording");
    await expect(cell(row, "line-price")).toHaveText("150.00");
    await expect(cell(row, "line-net")).toHaveText("150.00");
    await expect(cell(row, "line-vat")).toHaveText("9.00");
    expect(testRow(`select name || '|' || unit || '|' || price_ex_vat::text || '|' || vat_rate::text || '|' || description || '|' || updated_at::text from items where id = ${sql(item.id)}`)).toBe(itemBefore);
    await expect(row.getByRole("link", { name: "Catalog item" })).toBeVisible(); // still linked to its item
  });

  test("a rejected edit keeps the draft open and the stored line unchanged", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    const line = await addLine(context, ORG_A.id, transaction.id, { description: "Keep", unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" });
    await page.goto(url);

    await rows(page).first().getByTestId("edit-line").click();
    await page.getByLabel("Description", { exact: true }).fill("");
    await quantity(page).fill("0");
    await page.getByTestId("save-line").click();

    await expect(page.getByTestId("error-description")).toBeVisible();
    await expect(page.getByTestId("error-quantity")).toBeVisible();
    await expect(quantity(page)).toHaveValue("0");
    expect(testRow(`select description || '|' || quantity::text || '|' || version from transaction_lines where id = ${sql(line.id)}`)).toBe("Keep|1.000|1");
  });

  test("a line can be deleted after confirming, and is gone from the server", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await addLine(context, ORG_A.id, transaction.id, { description: "Doomed", unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" });
    await addLine(context, ORG_A.id, transaction.id, { description: "Stays", unit: "u", quantity: "1", unit_price_ex_vat: "20.00", vat_rate: "25" });
    await page.goto(url);

    await rowFor(page, "Doomed").getByTestId("delete-line").click();
    await rowFor(page, "Doomed").getByTestId("delete-line-keep").click();
    await expect(rows(page)).toHaveCount(2);

    await rowFor(page, "Doomed").getByTestId("delete-line").click();
    await rowFor(page, "Doomed").getByTestId("delete-line-confirm").click();

    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("Stays");
    expect(testRow(`select count(*) from transaction_lines where transaction_id = ${sql(transaction.id)} and description = 'Doomed'`)).toBe("0");
  });
});

test.describe("the header", () => {
  test("date and customer can be changed while a draft, and persist", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    const other = await createCustomer(context, ORG_A.id, unique("Other Billing"));
    await page.goto(url);

    await page.getByTestId("edit-header").click();
    await pick(page, "billing_customer_id", other.name);
    await page.getByLabel("Date").fill("2026-11-02");
    await page.getByTestId("save-header").click();

    await expect(page.getByTestId("header-customer")).toContainText(other.name);
    await expect(page.getByTestId("header-date")).toHaveText("2026-11-02");
    expect(testRow(`select billing_customer_id || '|' || transaction_date::text || '|' || header_version from transactions where id = ${sql(transaction.id)}`)).toBe(`${other.id}|2026-11-02|2`);
  });

  test("a billing customer deactivated later is still shown, and the other header field can still be edited", async ({ page, context }) => {
    const { customer, transaction, url } = await newDraft(context);
    await setActive(context, ORG_A.id, "customers", customer.id, false);
    await page.goto(url);
    await expect(page.getByTestId("header-customer")).toContainText("(inactive)");

    await page.getByTestId("edit-header").click();
    await expect(picker(page, "billing_customer_id").getByRole("combobox")).toHaveValue(`${customer.name} (inactive)`);
    const request = page.waitForRequest((r) => r.method() === "PATCH");
    await page.getByLabel("Date").fill("2026-12-01");
    await page.getByTestId("save-header").click();

    expect(JSON.parse((await request).postData() ?? "")).toEqual({ transaction_date: "2026-12-01" });
    await expect(page.getByTestId("header-date")).toHaveText("2026-12-01");
    expect(testRow(`select billing_customer_id from transactions where id = ${sql(transaction.id)}`)).toBe(customer.id);
  });

  test("choosing a customer who became inactive is refused on the customer control", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    const other = await createCustomer(context, ORG_A.id, unique("Becomes Inactive"));
    await page.goto(url);
    await page.getByTestId("edit-header").click();
    await pick(page, "billing_customer_id", other.name);
    await setActive(context, ORG_A.id, "customers", other.id, false);

    await page.getByTestId("save-header").click();

    await expect(page.getByTestId("error-billing_customer_id")).toContainText("inactive");
    expect(testRow(`select billing_customer_id from transactions where id = ${sql(transaction.id)}`)).toBe(transaction.billing_customer_id);
  });
});

test.describe("lifecycle", () => {
  test("an empty draft cannot be completed: the backend's reason is shown and the draft stays a draft", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await page.goto(url);

    await page.getByTestId("complete").click();

    await expect(page.getByTestId("editor-notice")).toContainText("at least one line");
    await expect(page.getByTestId("tx-status")).toHaveText("Draft");
    expect(testRow(`select status from transactions where id = ${sql(transaction.id)}`)).toBe("draft");
  });

  test("complete → read-only → reopen → editable → cancel (after confirming) → final", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await addLine(context, ORG_A.id, transaction.id, { description: "Billable", unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" });
    await page.goto(url);

    await page.getByTestId("complete").click();
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");
    expect(testRow(`select status from transactions where id = ${sql(transaction.id)}`)).toBe("completed");
    for (const id of ["edit-header", "add-line", "edit-line", "delete-line", "complete"]) await expect(page.getByTestId(id)).toHaveCount(0);
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await expect(rows(page)).toHaveCount(1); // still shown in full, read-only
    await page.reload();
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");

    await page.getByTestId("reopen").click();
    await expect(page.getByTestId("tx-status")).toHaveText("Draft");
    await expect(page.getByTestId("edit-header")).toBeVisible();
    await expect(page.getByTestId("add-line")).toBeVisible();

    await page.getByTestId("cancel").click();
    await page.getByTestId("cancel-keep").click();
    await expect(page.getByTestId("tx-status")).toHaveText("Draft"); // Keep: nothing happened
    await page.getByTestId("cancel").click();
    await page.getByTestId("cancel-confirm").click();
    await expect(page.getByTestId("tx-status")).toHaveText("Cancelled");
    await expect(page.getByTestId("lifecycle")).toHaveText("No further actions.");
    for (const id of ["complete", "reopen", "cancel", "edit-header", "add-line", "edit-line", "delete-line"]) await expect(page.getByTestId(id)).toHaveCount(0);
    expect(testRow(`select status from transactions where id = ${sql(transaction.id)}`)).toBe("cancelled");
  });

  test("a completed or cancelled transaction cannot be changed even by a direct request, and the page agrees", async ({ context }) => {
    const { transaction } = await newDraft(context);
    const line = await addLine(context, ORG_A.id, transaction.id, { description: "Locked", unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" });
    const done = await lifecycle(context, ORG_A.id, transaction.id, "complete");

    const edit = await context.request.patch(bffUrl(ORG_A.id, `/transactions/${transaction.id}/lines/${line.id}`), { data: { quantity: "9" }, headers: ifMatch(1) });
    const header = await context.request.patch(bffUrl(ORG_A.id, `/transactions/${transaction.id}`), { data: { transaction_date: "2030-01-01" }, headers: ifMatch(done.header_version) });
    expect([edit.status(), header.status()]).toEqual([409, 409]);
    expect(await edit.text()).toContain("completed transaction cannot be");

    const cancelled = await lifecycle(context, ORG_A.id, transaction.id, "cancel");
    const reopen = await context.request.post(bffUrl(ORG_A.id, `/transactions/${transaction.id}/reopen`), { headers: ifMatch(cancelled.version) });
    expect(reopen.status()).toBe(409);
    expect(testRow(`select status || '|' || (select quantity::text from transaction_lines where id = ${sql(line.id)}) from transactions where id = ${sql(transaction.id)}`)).toBe("cancelled|1.000");
  });

  test("blocked completion keeps the structured problems and shows them, with the line named", async ({ page, context }) => {
    const { transaction, url } = await newDraft(context);
    await addLine(context, ORG_A.id, transaction.id, { description: "Needs a project", unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" });

    await withRequiredTransactionField(context, ORG_A.id, async (label) => {
      await page.goto(url);
      await page.getByTestId("complete").click();

      await expect(page.getByTestId("editor-notice")).toContainText("blocked");
      await expect(page.getByTestId("editor-problems")).toContainText(`Transaction · ${label}`);
      await expect(page.getByTestId("tx-status")).toHaveText("Draft");
      expect(testRow(`select status from transactions where id = ${sql(transaction.id)}`)).toBe("draft");
    });

    // With the field switched off again the same transaction completes.
    await page.reload();
    await page.getByTestId("complete").click();
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");
  });

  test("the list shows status and totals, filters by status, customer and date, and pages", async ({ page, context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("List Billing"));
    const make = (date: string, price: string) =>
      createTransaction(context, ORG_A.id, { billing_customer_id: customer.id, transaction_date: date, lines: [{ description: "L", unit: "u", quantity: "1", unit_price_ex_vat: price, vat_rate: "25" }] });
    const old = await make("2026-01-15", "10.00");
    const mid = await make("2026-06-15", "20.00");
    const recent = await make("2026-10-15", "30.00");
    await lifecycle(context, ORG_A.id, mid.id, "complete");
    await lifecycle(context, ORG_A.id, recent.id, "cancel");

    const base = `${list}?billing_customer_id=${customer.id}`;
    await page.goto(base);
    await expect(page.getByTestId("transaction-row")).toHaveCount(3);
    expect(await page.getByTestId("transaction-link").allTextContents()).toEqual(["2026-10-15", "2026-06-15", "2026-01-15"]); // newest first
    const middle = page.getByTestId("transaction-row").nth(1);
    await expect(middle.getByTestId("transaction-net")).toHaveText("20.00");
    await expect(middle.getByTestId("transaction-gross")).toHaveText("25.00");
    await expect(middle.getByTestId("tx-status")).toHaveText("Completed");

    await page.goto(`${base}&status=draft`);
    await expect(page.getByTestId("transaction-row")).toHaveCount(1);
    await expect(page.getByTestId("transaction-link")).toHaveText(old.transaction_date);
    await page.goto(`${base}&date_from=2026-06-01&date_to=2026-07-01`);
    await expect(page.getByTestId("transaction-row")).toHaveCount(1);
    await expect(page.getByTestId("transaction-link")).toHaveText("2026-06-15");
    await page.goto(`${base}&status=cancelled&date_from=2026-06-01`);
    await expect(page.getByTestId("transaction-row")).toHaveCount(1);
    await expect(page.getByTestId("picker-billing_customer_id").getByRole("combobox")).toHaveValue(customer.name);

    // The filter form puts the choices in the address.
    await page.goto(list);
    await page.getByLabel("Status").selectOption("completed");
    await page.getByLabel("From").fill("2026-06-01");
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page).toHaveURL(/status=completed/);
    await expect(page).toHaveURL(/date_from=2026-06-01/);
  });

  test("a hostile or malformed filter in the address is ignored", async ({ page }) => {
    const response = await page.goto(`${list}?status=deleted&date_from=yesterday&date_to=%27%3B--&billing_customer_id=not-a-uuid&page=-4`);
    expect(response?.status()).toBe(200);
    await expect(page.getByLabel("Status")).toHaveValue("");
  });

  test("pages through more than one page of transactions", async ({ page, context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("Paging Billing"));
    for (let index = 0; index < 26; index += 1) await createTransaction(context, ORG_A.id, { billing_customer_id: customer.id, transaction_date: "2026-05-05" });

    await page.goto(`${list}?billing_customer_id=${customer.id}`);
    await expect(page.getByTestId("transaction-row")).toHaveCount(25);
    await page.getByRole("link", { name: "Next" }).click();
    await expect(page.getByTestId("transaction-row")).toHaveCount(1);
    await expect(page.getByTestId("page-number")).toHaveText("Page 2");
    await page.getByRole("link", { name: "Previous" }).click();
    await expect(page.getByTestId("transaction-row")).toHaveCount(25);
  });

  test("the main navigation reaches Transactions", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}`);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Transactions" }).click();
    await expect(page).toHaveURL(list);
    await page.getByTestId("new-transaction").click();
    await expect(page).toHaveURL(`${list}/new`);
  });
});
