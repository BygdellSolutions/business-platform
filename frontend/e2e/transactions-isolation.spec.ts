import { expect, test, type APIResponse, type Page } from "./fixtures";

import { BASE_URL } from "./env";
import {
  addLine,
  bffUrl,
  choices,
  createCustomer,
  createItem,
  createTransaction,
  FREDRIK,
  getTransaction,
  ifMatch,
  lifecycle,
  MARIA,
  openAddLine,
  ORG_A,
  ORG_B,
  picker,
  RANDOM_ORG,
  signIn,
  sql,
  testRow,
  unique,
} from "./support";

/**
 * Tenant isolation for transactions, lines, versions and the pickers they use, as the browser
 * and the BFF experience it. Both organizations get a customer, an item, a transaction and a
 * line with the SAME names next to records that exist in one organization only (and different
 * prices), so a mix-up shows in page text, a submitted id or the database.
 */

const TAG = unique("TIso");
const DAY = "2031-03-03";
const TWIN_CUSTOMER = `${TAG} Twin Billing`;
const TWIN_ITEM = `${TAG} Twin Item`;
const ONLY_A_CUSTOMER = `${TAG} Only A Billing`;
const ONLY_B_CUSTOMER = `${TAG} Only B Billing`;
const ONLY_A_ITEM = `${TAG} Only A Item`;
const ONLY_B_ITEM = `${TAG} Only B Item`;

interface Side {
  customer: string;
  onlyCustomer: string;
  item: string;
  onlyItem: string;
  tx: string;
  itemLine: string;
  adHocLine: string;
  onlyTx: string;
}
const ids: { a: Side; b: Side } = {
  a: { customer: "", onlyCustomer: "", item: "", onlyItem: "", tx: "", itemLine: "", adHocLine: "", onlyTx: "" },
  b: { customer: "", onlyCustomer: "", item: "", onlyItem: "", tx: "", itemLine: "", adHocLine: "", onlyTx: "" },
};

const txUrl = (orgId: string, id: string) => `/o/${orgId}/transactions/${id}`;
const rows = (page: Page) => page.getByTestId("line-row");
const switchTo = (page: Page, org: { name: string }) => page.getByTestId("org-switcher").getByRole("link", { name: org.name }).click();

async function outcome(response: APIResponse) {
  return { status: response.status(), body: await response.text() };
}

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL });
  await signIn(context, FREDRIK);
  for (const [key, org] of [["a", ORG_A], ["b", ORG_B]] as const) {
    const side = key === "a" ? "A" : "B";
    const mine = ids[key];
    mine.customer = (await createCustomer(context, org.id, TWIN_CUSTOMER)).id;
    mine.onlyCustomer = (await createCustomer(context, org.id, `${TAG} Only ${side} Billing`)).id;
    mine.item = (await createItem(context, org.id, { name: TWIN_ITEM, price_ex_vat: key === "a" ? "11.11" : "22.22", vat_rate: "25" })).id;
    mine.onlyItem = (await createItem(context, org.id, { name: `${TAG} Only ${side} Item` })).id;
    const tx = await createTransaction(context, org.id, { billing_customer_id: mine.customer, transaction_date: DAY });
    mine.tx = tx.id;
    mine.itemLine = (await addLine(context, org.id, tx.id, { item_id: mine.item, quantity: "1" })).id;
    mine.adHocLine = (await addLine(context, org.id, tx.id, { description: `${TAG} Twin Line`, unit: "u", quantity: "1", unit_price_ex_vat: key === "a" ? "5.00" : "6.00", vat_rate: "25" })).id;
    mine.onlyTx = (await createTransaction(context, org.id, { billing_customer_id: mine.onlyCustomer, transaction_date: DAY })).id;
  }
  await context.close();
});

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

test.describe("identical-looking transactions stay separate", () => {
  test("each organization lists only its own, with its own totals, linking to its own records", async ({ page }) => {
    for (const [org, own, other, net] of [[ORG_A, ids.a, ids.b, "16.11"], [ORG_B, ids.b, ids.a, "28.22"]] as const) {
      await page.goto(`/o/${org.id}/transactions?date_from=${DAY}&date_to=${DAY}`);
      await expect(page.getByTestId("org-name")).toHaveText(org.name);
      const hrefs = await page.getByTestId("transaction-link").evaluateAll((links) => links.map((link) => link.getAttribute("href")));
      expect(hrefs.sort()).toEqual([txUrl(org.id, own.tx), txUrl(org.id, own.onlyTx)].sort());
      expect(hrefs.join()).not.toContain(other.tx);

      const twin = page.getByTestId("transaction-row").filter({ has: page.locator(`a[href="${txUrl(org.id, own.tx)}"]`) });
      await expect(twin.getByTestId("transaction-net")).toHaveText(net); // this organization's lines only
      const customerHref = await twin.getByTestId("transaction-customer").getByRole("link").getAttribute("href");
      expect(customerHref).toBe(`/o/${org.id}/customers/${own.customer}`);
    }
  });

  test("a twin transaction's page shows its own lines, prices and customer", async ({ page }) => {
    for (const [org, own, price, adHoc] of [[ORG_A, ids.a, "11.11", "5.00"], [ORG_B, ids.b, "22.22", "6.00"]] as const) {
      await page.goto(txUrl(org.id, own.tx));
      await expect(page.getByTestId("org-name")).toHaveText(org.name);
      await expect(rows(page)).toHaveCount(2);
      await expect(rows(page).filter({ hasText: TWIN_ITEM }).getByTestId("line-price")).toHaveText(price);
      await expect(rows(page).filter({ hasText: "Twin Line" }).getByTestId("line-price")).toHaveText(adHoc);
      expect(await page.getByTestId("header-customer").getByRole("link").getAttribute("href")).toBe(`/o/${org.id}/customers/${own.customer}`);
    }
  });

  test("editing, completing or deleting a line in one organization leaves the other's twin untouched", async ({ page, context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("Edit Twin Billing"));
    const aTx = await createTransaction(context, ORG_A.id, { billing_customer_id: customer.id, transaction_date: "2031-04-04" });
    const bCustomer = await createCustomer(context, ORG_B.id, unique("Edit Twin Billing B"));
    const bTx = await createTransaction(context, ORG_B.id, { billing_customer_id: bCustomer.id, transaction_date: "2031-04-04" });
    for (const [org, tx] of [[ORG_A, aTx], [ORG_B, bTx]] as const) await addLine(context, org.id, tx.id, { description: "Same", unit: "u", quantity: "1", unit_price_ex_vat: "9.00", vat_rate: "25" });
    const bBefore = testRow(`select t.status || '|' || t.version || '|' || l.description || ':' || l.unit_price_ex_vat::text || ':' || l.version from transactions t join transaction_lines l on l.transaction_id = t.id where t.id = ${sql(bTx.id)}`);

    await page.goto(txUrl(ORG_A.id, aTx.id));
    await rows(page).first().getByTestId("edit-line").click();
    await page.getByLabel("Description", { exact: true }).fill("Changed in A");
    await page.getByLabel("Unit price excluding VAT", { exact: true }).fill("99.00");
    await page.getByTestId("save-line").click();
    await expect(rows(page).first()).toContainText("Changed in A");
    await page.getByTestId("complete").click();
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");

    expect(testRow(`select t.status || '|' || t.version || '|' || l.description || ':' || l.unit_price_ex_vat::text || ':' || l.version from transactions t join transaction_lines l on l.transaction_id = t.id where t.id = ${sql(bTx.id)}`)).toBe(bBefore);
  });
});

test.describe("direct navigation to another organization's transaction", () => {
  async function pageOutcome(page: Page, url: string) {
    const response = await page.goto(url);
    await expect(page.getByTestId("not-found")).toBeVisible();
    return { status: response?.status(), text: await page.locator("body").innerText() };
  }

  test("a foreign transaction id is the same not-found as a random or a malformed one, and reveals nothing", async ({ page }) => {
    const random = await pageOutcome(page, txUrl(ORG_A.id, RANDOM_ORG));
    const malformed = await pageOutcome(page, txUrl(ORG_A.id, "not-a-uuid"));
    const foreign = await pageOutcome(page, txUrl(ORG_A.id, ids.b.tx));

    expect(foreign).toEqual(random);
    expect(malformed).toEqual(random);
    for (const text of [TWIN_CUSTOMER, TWIN_ITEM, "22.22", ORG_B.name]) expect(foreign.text).not.toContain(text);
  });

  test("a member of B only sees the same not-found for every Transactions page of organization A", async ({ page, context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);
    const reference = await pageOutcome(page, `/o/${RANDOM_ORG}/transactions`);
    for (const path of [`/o/${ORG_A.id}/transactions`, `/o/${ORG_A.id}/transactions/new`, txUrl(ORG_A.id, ids.a.tx)]) expect(await pageOutcome(page, path)).toEqual(reference);
    await pageOutcome(page, txUrl(ORG_B.id, ids.a.tx)); // and A's transaction under B's address
  });
});

test.describe("mutating another organization's records, with and without a version", () => {
  const operations = (tx: string, line: string) =>
    [
      ["patch", `/transactions/${tx}`, { transaction_date: "2032-01-01" }],
      ["delete", `/transactions/${tx}`, undefined],
      ["post", `/transactions/${tx}/complete`, undefined],
      ["post", `/transactions/${tx}/reopen`, undefined],
      ["post", `/transactions/${tx}/cancel`, undefined],
      ["post", `/transactions/${tx}/lines`, { description: "Smuggled", unit: "u", quantity: "1", unit_price_ex_vat: "1.00", vat_rate: "25" }],
      ["patch", `/transactions/${tx}/lines/${line}`, { quantity: "9" }],
      ["delete", `/transactions/${tx}/lines/${line}`, undefined],
    ] as const;

  test("every operation on a foreign transaction or line is the same 404 as for a random one, whatever the If-Match says", async ({ context }) => {
    const before = await getTransaction(context, ORG_B.id, ids.b.tx);
    const headers: Record<string, string>[] = [
      {}, // none
      ifMatch(before.version), // B's real transaction version
      ifMatch(before.header_version),
      ifMatch(before.lines[0].version),
      ifMatch(999),
      { "if-match": "garbage" },
    ];

    for (const extra of headers) {
      for (const [index, [method, path, data]] of operations(ids.b.tx, ids.b.itemLine).entries()) {
        const [, randomPath] = operations(RANDOM_ORG, RANDOM_ORG)[index];
        const viaA = await context.request[method](bffUrl(ORG_A.id, path), { data, headers: extra });
        const random = await context.request[method](bffUrl(ORG_A.id, randomPath), { data, headers: extra });
        const got = await outcome(viaA);
        const wanted = await outcome(random);

        if (extra["if-match"] === "garbage") {
          // The BFF itself refuses a malformed header before anything is asked: the same for both.
          expect(got).toEqual(wanted);
          expect(got.status).toBe(400);
        } else {
          expect(got.status, `${method} ${path} with ${JSON.stringify(extra)}`).toBe(404);
          expect(got).toEqual(wanted);
          expect(got.body).toBe('{"detail":"Not found"}');
        }
      }
    }
    expect(await getTransaction(context, ORG_B.id, ids.b.tx)).toEqual(before);
  });

  test("a foreign line id under MY transaction is a 404 too, and moves nothing", async ({ context }) => {
    const mine = await getTransaction(context, ORG_A.id, ids.a.tx);
    for (const [method, data] of [["patch", { quantity: "9" }], ["delete", undefined]] as const) {
      for (const lineId of [ids.b.itemLine, RANDOM_ORG]) {
        const response = await context.request[method](bffUrl(ORG_A.id, `/transactions/${ids.a.tx}/lines/${lineId}`), { data, headers: ifMatch(1) });
        expect(await outcome(response)).toEqual({ status: 404, body: '{"detail":"Not found"}' });
      }
    }
    expect(await getTransaction(context, ORG_A.id, ids.a.tx)).toEqual(mine);
  });

  test("a member of B only gets the same 404 for A's records through either address", async ({ context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);
    for (const org of [ORG_A, ORG_B]) {
      for (const [method, path, data] of operations(ids.a.tx, ids.a.itemLine)) {
        const response = await context.request[method](bffUrl(org.id, path), { data, headers: ifMatch(1) });
        expect(response.status(), `${method} ${path} via ${org.name}`).toBe(404);
      }
    }
    expect(testRow(`select status || '|' || version from transactions where id = ${sql(ids.a.tx)}`)).toBe("draft|3");
  });
});

test.describe("references to other organizations' customers and items", () => {
  test("a foreign customer or item, and a random one, are refused identically and stored nowhere", async ({ context }) => {
    const before = testRow(`select (select count(*) from transactions where organization_id = ${sql(ORG_A.id)}) || '|' || (select count(*) from transaction_lines where organization_id = ${sql(ORG_A.id)})`);

    const create = async (id: string) => outcome(await context.request.post(bffUrl(ORG_A.id, "/transactions"), { data: { billing_customer_id: id, transaction_date: DAY } }));
    expect(await create(ids.b.customer)).toEqual(await create(RANDOM_ORG));
    expect((await create(ids.b.customer)).status).toBe(422);

    const header = async (id: string) => outcome(await context.request.patch(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}`), { data: { billing_customer_id: id }, headers: ifMatch((await getTransaction(context, ORG_A.id, ids.a.tx)).header_version) }));
    const viaForeign = await header(ids.b.customer);
    expect(viaForeign.status).toBe(422);
    expect(viaForeign).toEqual(await header(RANDOM_ORG));

    const line = async (itemId: string) => outcome(await context.request.post(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}/lines`), { data: { item_id: itemId, quantity: "1" } }));
    const itemForeign = await line(ids.b.item);
    expect(itemForeign.status).toBe(422);
    expect(itemForeign).toEqual(await line(RANDOM_ORG));
    expect(itemForeign.body).not.toContain(TWIN_ITEM);

    expect(testRow(`select (select count(*) from transactions where organization_id = ${sql(ORG_A.id)}) || '|' || (select count(*) from transaction_lines where organization_id = ${sql(ORG_A.id)})`)).toBe(before);
  });

  test("the customer and item pickers never offer the other organization's records", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}/transactions/new`);
    await picker(page, "billing_customer_id").getByRole("combobox").fill(TAG);
    await expect(picker(page, "billing_customer_id").getByRole("option")).toHaveCount(2);
    const offered = (await choices(page, "billing_customer_id")).join("|");
    expect(offered).toContain(ONLY_A_CUSTOMER);
    expect(offered).not.toContain(ONLY_B_CUSTOMER);
    await picker(page, "billing_customer_id").getByRole("combobox").fill(ONLY_B_CUSTOMER);
    await expect(picker(page, "billing_customer_id").getByText("No matches")).toBeVisible();

    await page.goto(txUrl(ORG_A.id, ids.a.tx));
    await openAddLine(page);
    await picker(page, "item_id").getByRole("combobox").fill(TAG);
    await expect(picker(page, "item_id").getByRole("option")).toHaveCount(2);
    const items = (await choices(page, "item_id")).join("|");
    expect(items).toContain(ONLY_A_ITEM);
    expect(items).toContain("11.11");
    expect(items).not.toContain(ONLY_B_ITEM);
    expect(items).not.toContain("22.22");
    await picker(page, "item_id").getByRole("combobox").fill(ONLY_B_ITEM);
    await expect(picker(page, "item_id").getByText("No matches")).toBeVisible();
  });

  test("choosing the twin item in each organization adds a line with that organization's snapshot", async ({ page, context }) => {
    for (const [org, price] of [[ORG_A, "11.11"], [ORG_B, "22.22"]] as const) {
      const customer = await createCustomer(context, org.id, unique("Twin Pick Billing"));
      const tx = await createTransaction(context, org.id, { billing_customer_id: customer.id, transaction_date: "2031-05-05" });
      await page.goto(txUrl(org.id, tx.id));
      await openAddLine(page);
      await picker(page, "item_id").getByRole("combobox").fill(TWIN_ITEM);
      await expect(picker(page, "item_id").getByRole("option")).toHaveCount(1); // never two
      await picker(page, "item_id").getByRole("option").click();
      await page.getByLabel("Quantity", { exact: true }).fill("1");
      await page.getByTestId("submit-line").click();

      await expect(rows(page).first().getByTestId("line-price")).toHaveText(price);
      expect(testRow(`select organization_id || '|' || item_id from transaction_lines where transaction_id = ${sql(tx.id)}`)).toBe(`${org.id}|${org === ORG_A ? ids.a.item : ids.b.item}`);
    }
  });
});

test.describe("switching organizations while editing", () => {
  test("open editors and drafts do not follow the user to the other organization", async ({ page }) => {
    await page.goto(txUrl(ORG_A.id, ids.a.tx));
    await rows(page).first().getByTestId("edit-line").click(); // a catalog line: the discount is what can be typed
    await page.getByLabel("Discount % (optional)").fill("37.5");
    await page.getByTestId("edit-header").click();
    await page.getByLabel("Date").fill("2040-04-04");

    await switchTo(page, ORG_B);
    await expect(page).toHaveURL(`/o/${ORG_B.id}`);
    await page.goto(txUrl(ORG_B.id, ids.b.tx));

    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    await expect(page.getByTestId("line-editor")).toHaveCount(0);
    await expect(page.getByTestId("save-header")).toHaveCount(0);
    expect(await page.content()).not.toContain("37.5");
    expect(await page.content()).not.toContain("2040-04-04");
    await expect(page.getByTestId("complete")).toBeEnabled(); // no leftover "editor open" state
  });

  test("two tabs, two organizations: each saves into its own, and a conflict in one does not touch the other", async ({ context }) => {
    const customerA = await createCustomer(context, ORG_A.id, unique("Tabs A"));
    const customerB = await createCustomer(context, ORG_B.id, unique("Tabs B"));
    const txA = await createTransaction(context, ORG_A.id, { billing_customer_id: customerA.id, transaction_date: "2031-06-06" });
    const txB = await createTransaction(context, ORG_B.id, { billing_customer_id: customerB.id, transaction_date: "2031-06-06" });
    const lineA = await addLine(context, ORG_A.id, txA.id, { description: "Tab line", unit: "u", quantity: "1", unit_price_ex_vat: "1.00", vat_rate: "25" });
    const lineB = await addLine(context, ORG_B.id, txB.id, { description: "Tab line", unit: "u", quantity: "1", unit_price_ex_vat: "1.00", vat_rate: "25" });
    const tabA = await context.newPage();
    const tabB = await context.newPage();
    await tabA.goto(txUrl(ORG_A.id, txA.id));
    await tabB.goto(txUrl(ORG_B.id, txB.id));

    for (const [tab, text] of [[tabA, "Saved in A"], [tabB, "Saved in B"]] as const) {
      await rows(tab).first().getByTestId("edit-line").click();
      await tab.getByLabel("Description", { exact: true }).fill(text);
    }
    await tabB.getByTestId("save-line").click();
    await expect(rows(tabB).first()).toContainText("Saved in B");
    await tabA.getByTestId("save-line").click();
    await expect(rows(tabA).first()).toContainText("Saved in A");

    expect(testRow(`select description || '|' || organization_id from transaction_lines where id = ${sql(lineA.id)}`)).toBe(`Saved in A|${ORG_A.id}`);
    expect(testRow(`select description || '|' || organization_id from transaction_lines where id = ${sql(lineB.id)}`)).toBe(`Saved in B|${ORG_B.id}`);
    await expect(tabA.getByTestId("org-name")).toHaveText(ORG_A.name);
    await expect(tabB.getByTestId("org-name")).toHaveText(ORG_B.name);
  });

  test("back and forward across a switch show the organization in the address, never the other's lines", async ({ page }) => {
    await page.goto(txUrl(ORG_A.id, ids.a.tx));
    await expect(rows(page).filter({ hasText: TWIN_ITEM }).getByTestId("line-price")).toHaveText("11.11");
    await switchTo(page, ORG_B);
    await page.goto(txUrl(ORG_B.id, ids.b.tx));
    await expect(rows(page).filter({ hasText: TWIN_ITEM }).getByTestId("line-price")).toHaveText("22.22");

    await page.goBack();
    await page.goBack();
    await expect(page).toHaveURL(txUrl(ORG_A.id, ids.a.tx));
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);
    await expect(rows(page).filter({ hasText: TWIN_ITEM }).getByTestId("line-price")).toHaveText("11.11");

    await page.goForward();
    await page.goForward();
    await expect(page).toHaveURL(txUrl(ORG_B.id, ids.b.tx));
    await expect(rows(page).filter({ hasText: TWIN_ITEM }).getByTestId("line-price")).toHaveText("22.22");
    expect(await page.locator("main").innerText()).not.toContain("11.11");
  });
});

test.describe("forged headers", () => {
  const FORGED = { "x-organization-id": ORG_B.id, "x-dev-user-email": MARIA, authorization: "Bearer forged", "x-forwarded-for": "10.0.0.1" };

  test("a forged organization or identity cannot move a versioned change to another organization", async ({ context }) => {
    const customer = await createCustomer(context, ORG_A.id, unique("Forged Billing"));
    const tx = await createTransaction(context, ORG_A.id, { billing_customer_id: customer.id, transaction_date: "2031-07-07" });
    const line = await addLine(context, ORG_A.id, tx.id, { description: "Forged", unit: "u", quantity: "1", unit_price_ex_vat: "1.00", vat_rate: "25" });
    const current = await getTransaction(context, ORG_A.id, tx.id);

    const edit = await context.request.patch(bffUrl(ORG_A.id, `/transactions/${tx.id}/lines/${line.id}`), { headers: { ...FORGED, ...ifMatch(current.lines[0].version) }, data: { quantity: "2" } });
    expect(edit.status()).toBe(200);
    expect(testRow(`select quantity::text || '|' || organization_id from transaction_lines where id = ${sql(line.id)}`)).toBe(`2.000|${ORG_A.id}`);
    const done = await context.request.post(bffUrl(ORG_A.id, `/transactions/${tx.id}/complete`), { headers: { ...FORGED, ...ifMatch(current.version + 1) } });
    expect(done.status()).toBe(200);

    const smuggled = await context.request.post(bffUrl(ORG_A.id, "/transactions"), { headers: FORGED, data: { billing_customer_id: ids.b.customer, transaction_date: DAY } });
    expect(smuggled.status()).toBe(422); // B's customer is foreign to the request's real organization (A)
  });

  test("a forged identity cannot change A's records for a member of B only", async ({ context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);
    const asFredrik = { ...FORGED, "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id, ...ifMatch(1) };
    const before = testRow(`select version from transactions where id = ${sql(ids.a.tx)}`);

    const statuses = [
      (await context.request.patch(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}`), { headers: asFredrik, data: { transaction_date: "2040-01-01" } })).status(),
      (await context.request.post(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}/cancel`), { headers: asFredrik })).status(),
      (await context.request.delete(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}/lines/${ids.a.adHocLine}`), { headers: asFredrik })).status(),
    ];

    expect(statuses).toEqual([404, 404, 404]);
    expect(testRow(`select version from transactions where id = ${sql(ids.a.tx)}`)).toBe(before);
  });

  test("without the session cookie nothing is reachable, versioned or not", async ({ request }) => {
    const headers = { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id, ...ifMatch(1) };
    expect((await request.patch(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}`), { headers, data: { transaction_date: "2040-01-01" } })).status()).toBe(401);
    expect((await request.post(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}/complete`), { headers })).status()).toBe(401);
    expect((await request.get(bffUrl(ORG_A.id, `/transactions/${ids.a.tx}`), { headers })).status()).toBe(401);
  });
});

test("after all of the above, no transaction or line in the database refers to another organization's records", async ({ context }) => {
  await lifecycle(context, ORG_B.id, ids.b.onlyTx, "cancel").catch(() => undefined); // keep a cancelled one around as well
  const mismatches = [
    "select count(*) from transactions t join customers c on c.id = t.billing_customer_id where c.organization_id <> t.organization_id",
    "select count(*) from transaction_lines l join transactions t on t.id = l.transaction_id where t.organization_id <> l.organization_id",
    "select count(*) from transaction_lines l join items i on i.id = l.item_id where i.organization_id <> l.organization_id",
  ];
  for (const query of mismatches) expect(testRow(query)).toBe("0");
  expect(testRow("select count(*) from transactions where version < 1 or header_version < 1")).toBe("0");
});
