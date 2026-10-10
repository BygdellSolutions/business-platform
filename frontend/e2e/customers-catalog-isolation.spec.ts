import { expect, test, type APIResponse, type Page } from "./fixtures";

import { BASE_URL } from "./env";
import { FREDRIK, MARIA, ORG_A, ORG_B, RANDOM_ORG, bffUrl, createCustomer, createItem, signIn, sql, testRow, unique } from "./support";

/**
 * Tenant isolation for the Customers and Catalog screens, as the browser experiences it.
 * Both organizations get records that look identical (same names, same email) next to records
 * that exist in one organization only, so any leak of data or of stale state across organizations
 * is visible in the page text, the address or the database.
 */

const TAG = unique("Iso");
const TWIN_CUSTOMER = `${TAG} Twin Customer`;
const TWIN_ITEM = `${TAG} Twin Item`;
const ONLY_A_CUSTOMER = `${TAG} Only A Customer`;
const ONLY_B_CUSTOMER = `${TAG} Only B Customer`;
const ONLY_A_ITEM = `${TAG} Only A Item`;
const ONLY_B_ITEM = `${TAG} Only B Item`;

const ids = {
  a: { customer: "", item: "", onlyCustomer: "", onlyItem: "" },
  b: { customer: "", item: "", onlyCustomer: "", onlyItem: "" },
};

const customers = (orgId: string, query = "") => `/o/${orgId}/customers${query}`;
const catalog = (orgId: string, query = "") => `/o/${orgId}/catalog${query}`;
const customerNames = (page: Page) => page.getByTestId("customer-row").locator("td:nth-child(2)").allTextContents() // after the No. column;
const itemNames = (page: Page) => page.getByTestId("item-row").locator("td:nth-child(2)").allTextContents() // after the No. column;
const switchTo = (page: Page, org: { name: string }) => page.getByTestId("org-switcher").getByRole("link", { name: org.name }).click();

async function outcome(response: APIResponse) {
  return { status: response.status(), body: await response.text() };
}

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL });
  await signIn(context, FREDRIK);

  for (const [key, org] of [["a", ORG_A], ["b", ORG_B]] as const) {
    const side = key === "a" ? "A" : "B";
    const twin = await createCustomer(context, org.id, TWIN_CUSTOMER);
    await context.request.patch(bffUrl(org.id, `/customers/${twin.id}`), { data: { email: "twin@example.test" } });
    ids[key].customer = twin.id;
    ids[key].onlyCustomer = (await createCustomer(context, org.id, `${TAG} Only ${side} Customer`)).id;
    // Same name, different price: a price from the wrong organization is visible at once.
    ids[key].item = (await createItem(context, org.id, { name: TWIN_ITEM, price_ex_vat: key === "a" ? "11.11" : "22.22", vat_rate: "25" })).id;
    ids[key].onlyItem = (await createItem(context, org.id, { name: `${TAG} Only ${side} Item`, type: "product" })).id;
  }
  await context.close();
});

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

test.describe("identical-looking records stay separate", () => {
  test("the same customer name in two organizations is two records, each listed only in its own organization", async ({ page }) => {
    expect(ids.a.customer).not.toBe(ids.b.customer);

    for (const [org, own, other] of [[ORG_A, ids.a, ids.b], [ORG_B, ids.b, ids.a]] as const) {
      await page.goto(customers(org.id, `?q=${encodeURIComponent(TWIN_CUSTOMER)}`));
      await expect(page.getByTestId("org-name")).toHaveText(org.name);
      await expect(page.getByTestId("customer-row")).toHaveCount(1);
      const href = (await page.getByTestId("customer-row").getByRole("link").getAttribute("href")) ?? "";
      expect(href).toBe(`/o/${org.id}/customers/${own.customer}`);
      expect(href).not.toContain(other.customer);
    }
  });

  test("the same item name with different prices shows each organization its own price", async ({ page }) => {
    await page.goto(catalog(ORG_A.id, `?q=${encodeURIComponent(TWIN_ITEM)}`));
    await expect(page.getByTestId("item-price")).toHaveText("11.11");
    await page.goto(catalog(ORG_B.id, `?q=${encodeURIComponent(TWIN_ITEM)}`));
    await expect(page.getByTestId("item-price")).toHaveText("22.22");

    await page.goto(`${catalog(ORG_A.id)}/${ids.a.item}`);
    await expect(page.getByLabel("Price excluding VAT", { exact: true })).toHaveValue("11.11");
    await page.goto(`${catalog(ORG_B.id)}/${ids.b.item}`);
    await expect(page.getByLabel("Price excluding VAT", { exact: true })).toHaveValue("22.22");
  });

  test("editing or deactivating one organization's twin leaves the other's untouched", async ({ page, context }) => {
    const name = unique("Edit Twin");
    const a = await createCustomer(context, ORG_A.id, name);
    const b = await createCustomer(context, ORG_B.id, name);
    const itemA = await createItem(context, ORG_A.id, { name, price_ex_vat: "5.00" });
    const itemB = await createItem(context, ORG_B.id, { name, price_ex_vat: "5.00" });

    await page.goto(`${customers(ORG_A.id)}/${a.id}`);
    await page.getByLabel("Name", { exact: true }).fill(`${name} renamed`);
    await page.getByLabel("Email", { exact: true }).fill("only-a@example.test");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    await page.getByRole("button", { name: "Deactivate customer" }).click();
    await expect(page.getByTestId("status")).toHaveText("Inactive");

    await page.goto(`${catalog(ORG_A.id)}/${itemA.id}`);
    await page.getByLabel("Price excluding VAT", { exact: true }).fill("9.99");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();

    expect(testRow(`select name || '|' || (email is null) || '|' || active from customers where id = ${sql(b.id)}`)).toBe(`${name}|true|true`);
    expect(testRow(`select price_ex_vat::text || '|' || active from items where id = ${sql(itemB.id)}`)).toBe("5.00|true");
    expect(testRow(`select organization_id from customers where id = ${sql(a.id)}`)).toBe(ORG_A.id);
  });
});

test.describe("search and filter results contain only the organization in the URL", () => {
  test("customers", async ({ page }) => {
    await page.goto(customers(ORG_A.id, `?q=${encodeURIComponent(TAG)}`));
    expect((await customerNames(page)).sort()).toEqual([ONLY_A_CUSTOMER, TWIN_CUSTOMER]);

    await page.goto(customers(ORG_B.id, `?q=${encodeURIComponent(TAG)}`));
    expect((await customerNames(page)).sort()).toEqual([ONLY_B_CUSTOMER, TWIN_CUSTOMER]);

    // The other organization's name finds nothing, with and without the status filter.
    for (const query of [`?q=${encodeURIComponent(ONLY_B_CUSTOMER)}`, `?q=${encodeURIComponent(ONLY_B_CUSTOMER)}&active=active`, `?q=${encodeURIComponent(ONLY_B_CUSTOMER)}&active=inactive`]) {
      await page.goto(customers(ORG_A.id, query));
      await expect(page.getByTestId("empty")).toBeVisible();
      await expect(page.getByTestId("customer-row")).toHaveCount(0);
    }
  });

  test("catalog, including the type and status filters", async ({ page }) => {
    await page.goto(catalog(ORG_A.id, `?q=${encodeURIComponent(TAG)}`));
    expect((await itemNames(page)).sort()).toEqual([ONLY_A_ITEM, TWIN_ITEM]);

    await page.goto(catalog(ORG_B.id, `?q=${encodeURIComponent(TAG)}&type=product`));
    expect(await itemNames(page)).toEqual([ONLY_B_ITEM]);

    await page.goto(catalog(ORG_A.id, `?q=${encodeURIComponent(ONLY_B_ITEM)}&type=product&active=active`));
    await expect(page.getByTestId("empty")).toBeVisible();
  });

  test("a customer id or organization id in the address is not a search or a way to widen one", async ({ page }) => {
    await page.goto(customers(ORG_A.id, `?q=${ids.b.customer}&organization_id=${ORG_B.id}&org=${ORG_B.id}`));
    await expect(page.getByTestId("empty")).toBeVisible();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);
  });
});

test.describe("direct navigation to another organization's record", () => {
  async function pageOutcome(page: Page, url: string) {
    const response = await page.goto(url);
    await expect(page.getByTestId("not-found")).toBeVisible();
    return { status: response?.status(), text: await page.locator("body").innerText() };
  }

  test("a foreign customer or item id is the same not-found as a random or malformed one", async ({ page }) => {
    for (const [area, foreign] of [["customers", ids.b.customer], ["catalog", ids.b.item]] as const) {
      const random = await pageOutcome(page, `/o/${ORG_A.id}/${area}/${RANDOM_ORG}`);
      const malformed = await pageOutcome(page, `/o/${ORG_A.id}/${area}/not-a-uuid`);
      const foreignOutcome = await pageOutcome(page, `/o/${ORG_A.id}/${area}/${foreign}`);

      expect(foreignOutcome).toEqual(random);
      expect(malformed).toEqual(random);
      for (const text of [TWIN_CUSTOMER, TWIN_ITEM, ORG_B.name, "22.22", "twin@example.test"]) expect(foreignOutcome.text).not.toContain(text);
    }
  });

  test("the same holds for the new-record and list pages of an organization that is not the user's", async ({ page, context }) => {
    await context.clearCookies();
    await signIn(context, MARIA); // employee of B only

    const reference = await pageOutcome(page, `/o/${RANDOM_ORG}/customers`);
    for (const path of [customers(ORG_A.id), `${customers(ORG_A.id)}/new`, `${customers(ORG_A.id)}/${ids.a.customer}`, catalog(ORG_A.id), `${catalog(ORG_A.id)}/new`, `${catalog(ORG_A.id)}/${ids.a.item}`]) {
      expect(await pageOutcome(page, path)).toEqual(reference);
    }
  });

  test("a record of organization A is not found under organization B's address, even by a member of both", async ({ page }) => {
    await pageOutcome(page, `${customers(ORG_B.id)}/${ids.a.customer}`);
    await pageOutcome(page, `${catalog(ORG_B.id)}/${ids.a.item}`);
  });
});

test.describe("edit attempts against foreign ids cannot mutate", () => {
  test("PATCH and DELETE through the BFF answer 404 exactly as for a random id, and change nothing", async ({ context }) => {
    const before = {
      customer: testRow(`select name || '|' || coalesce(email, '') || '|' || active from customers where id = ${sql(ids.b.customer)}`),
      item: testRow(`select name || '|' || price_ex_vat::text || '|' || active from items where id = ${sql(ids.b.item)}`),
    };
    const attempts: [string, "patch" | "delete", unknown][] = [
      ["customers", "patch", { name: "HACKED", email: "hacked@example.test" }],
      ["customers", "patch", { active: false }],
      ["customers", "delete", undefined],
      ["items", "patch", { name: "HACKED", price_ex_vat: "0.01" }],
      ["items", "patch", { active: false }],
      ["items", "delete", undefined],
    ];

    for (const [area, method, data] of attempts) {
      const foreign = area === "customers" ? ids.b.customer : ids.b.item;
      const viaA = await outcome(await context.request[method](bffUrl(ORG_A.id, `/${area}/${foreign}`), { data }));
      const random = await outcome(await context.request[method](bffUrl(ORG_A.id, `/${area}/${RANDOM_ORG}`), { data }));

      expect(viaA.status).toBe(404);
      expect(viaA).toEqual(random);
      expect(viaA.body).not.toContain("organization");
    }

    expect(testRow(`select name || '|' || coalesce(email, '') || '|' || active from customers where id = ${sql(ids.b.customer)}`)).toBe(before.customer);
    expect(testRow(`select name || '|' || price_ex_vat::text || '|' || active from items where id = ${sql(ids.b.item)}`)).toBe(before.item);
    expect(testRow(`select count(*) from customers where name = 'HACKED' or email = 'hacked@example.test'`)).toBe("0");
    expect(testRow(`select count(*) from items where name = 'HACKED'`)).toBe("0");
  });

  test("a member of B only cannot change A's records through either address", async ({ context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);
    const before = testRow(`select name || '|' || active from customers where id = ${sql(ids.a.customer)}`);

    for (const org of [ORG_A, ORG_B]) {
      const customer = await outcome(await context.request.patch(bffUrl(org.id, `/customers/${ids.a.customer}`), { data: { name: "HACKED", active: false } }));
      const item = await outcome(await context.request.patch(bffUrl(org.id, `/items/${ids.a.item}`), { data: { name: "HACKED" } }));
      expect([customer.status, item.status]).toEqual([404, 404]);
    }

    expect(testRow(`select name || '|' || active from customers where id = ${sql(ids.a.customer)}`)).toBe(before);
    expect(testRow(`select name from items where id = ${sql(ids.a.item)}`)).toBe(TWIN_ITEM);
  });

  test("an organization_id in a body is refused, not obeyed, and nothing is created anywhere", async ({ context }) => {
    const name = unique("Smuggled");
    const customer = await context.request.post(bffUrl(ORG_A.id, "/customers"), { data: { customer_type: "person", name, organization_id: ORG_B.id } });
    const item = await context.request.post(bffUrl(ORG_A.id, "/items"), { data: { type: "service", name, unit: "h", price_ex_vat: "1.00", vat_rate: "25", organization_id: ORG_B.id } });

    expect([customer.status(), item.status()]).toEqual([422, 422]);
    expect(testRow(`select (select count(*) from customers where name = ${sql(name)}) + (select count(*) from items where name = ${sql(name)})`)).toBe("0");
  });
});

test.describe("switching organizations", () => {
  test("a draft typed in the form of one organization is gone in the other", async ({ page }) => {
    await page.goto(`${customers(ORG_A.id)}/new`);
    await page.getByLabel("Name", { exact: true }).fill("Draft typed in A");
    await page.getByLabel("Email", { exact: true }).fill("draft-a@example.test");

    await switchTo(page, ORG_B);
    await expect(page).toHaveURL(`/o/${ORG_B.id}`);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Customers" }).click();
    await page.getByTestId("new-customer").click();

    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("Email", { exact: true })).toHaveValue("");
    expect(await page.content()).not.toContain("draft-a@example.test");
  });

  test("the catalog form's unsaved price does not follow the user to the other organization", async ({ page }) => {
    await page.goto(`${catalog(ORG_A.id)}/new`);
    await page.getByLabel("Price excluding VAT", { exact: true }).fill("4.35");

    await switchTo(page, ORG_B);
    await page.goto(`${catalog(ORG_B.id)}/new`);

    await expect(page.getByLabel("Price excluding VAT", { exact: true })).toHaveValue("");
  });

  test("a list and its search box show nothing of the previous organization after a switch", async ({ page }) => {
    await page.goto(customers(ORG_A.id, `?q=${encodeURIComponent(TAG)}`));
    expect(await customerNames(page)).toContain(ONLY_A_CUSTOMER);

    await switchTo(page, ORG_B);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Customers" }).click();

    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    await expect(page.getByRole("search").getByLabel("Search")).toHaveValue("");
    const text = await page.locator("main").innerText();
    expect(text).not.toContain(ONLY_A_CUSTOMER);
    const links = await page.getByTestId("customer-row").getByRole("link").evaluateAll((anchors) => anchors.map((anchor) => anchor.getAttribute("href") ?? ""));
    expect(links.length).toBeGreaterThan(0);
    for (const href of links) {
      expect(href.startsWith(`/o/${ORG_B.id}/customers/`)).toBe(true);
      expect(href).not.toContain(ids.a.customer);
      expect(href).not.toContain(ids.a.onlyCustomer);
    }
  });

  test("a save started in one organization lands there even if the user switches before it finishes", async ({ page }) => {
    const name = unique("In Flight");
    await page.route(`**/api/o/${ORG_A.id}/customers`, async (route) => {
      const response = await route.fetch(); // the request goes out at once...
      await new Promise((resolve) => setTimeout(resolve, 1500)); // ...the answer is slow
      await route.fulfill({ response }).catch(() => {}); // the page may be gone by then
    });
    await page.goto(`${customers(ORG_A.id)}/new`);
    await page.getByLabel("Name", { exact: true }).fill(name);

    await page.getByTestId("submit").click();
    await switchTo(page, ORG_B);
    await expect(page).toHaveURL(`/o/${ORG_B.id}`);

    await expect.poll(() => testRow(`select count(*) from customers where name = ${sql(name)}`)).toBe("1");
    expect(testRow(`select organization_id from customers where name = ${sql(name)}`)).toBe(ORG_A.id);
    await page.goto(customers(ORG_B.id, `?q=${encodeURIComponent(name)}`));
    await expect(page.getByTestId("empty")).toBeVisible();
  });
});

test.describe("two tabs", () => {
  test("each tab saves into its own organization, with the same name and email in both", async ({ context }) => {
    const name = unique("Two Tabs");
    const tabA = await context.newPage();
    const tabB = await context.newPage();
    await tabA.goto(`${customers(ORG_A.id)}/new`);
    await tabB.goto(`${customers(ORG_B.id)}/new`);

    for (const tab of [tabA, tabB]) {
      await tab.getByLabel("Name", { exact: true }).fill(name);
      await tab.getByLabel("Email", { exact: true }).fill("same@example.test");
    }
    await tabB.getByTestId("submit").click();
    await expect(tabB.getByTestId("created")).toBeVisible();
    await tabA.getByTestId("submit").click(); // after tab B has saved: still organization A
    await expect(tabA.getByTestId("created")).toBeVisible();

    expect(testRow(`select organization_id from customers where name = ${sql(name)} order by organization_id`)).toBe(`${ORG_A.id}\n${ORG_B.id}`);
    await expect(tabA).toHaveURL(new RegExp(`/o/${ORG_A.id}/customers/`));
    await expect(tabB).toHaveURL(new RegExp(`/o/${ORG_B.id}/customers/`));
    await expect(tabA.getByTestId("org-name")).toHaveText(ORG_A.name);
    await expect(tabB.getByTestId("org-name")).toHaveText(ORG_B.name);
  });

  test("an item saved in one tab never appears in the other tab's organization", async ({ context }) => {
    const name = unique("Two Tabs Item");
    const tabA = await context.newPage();
    const tabB = await context.newPage();
    await tabA.goto(`${catalog(ORG_A.id)}/new`);
    await tabB.goto(catalog(ORG_B.id, `?q=${encodeURIComponent(name)}`));

    await tabA.getByLabel("Name", { exact: true }).fill(name);
    await tabA.getByLabel("Unit", { exact: true }).fill("h");
    await tabA.getByLabel("Price excluding VAT", { exact: true }).fill("0.10");
    await tabA.getByLabel("VAT rate (%)", { exact: true }).fill("25");
    await tabA.getByTestId("submit").click();
    await expect(tabA.getByTestId("created")).toBeVisible();

    await tabB.reload();
    await expect(tabB.getByTestId("empty")).toBeVisible();
    expect(testRow(`select organization_id from items where name = ${sql(name)}`)).toBe(ORG_A.id);
  });
});

test.describe("browser history", () => {
  test("back and forward across an organization switch always show the organization in the address", async ({ page }) => {
    await page.goto(customers(ORG_A.id, `?q=${encodeURIComponent(TAG)}`));
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);
    await switchTo(page, ORG_B);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Customers" }).click();
    await page.getByRole("search").getByLabel("Search").fill(TAG);
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    expect((await customerNames(page)).sort()).toEqual([ONLY_B_CUSTOMER, TWIN_CUSTOMER]);

    const trail: string[] = [];
    for (let step = 0; step < 3; step += 1) {
      await page.goBack();
      trail.push(page.url());
      const org = page.url().includes(ORG_A.id) ? ORG_A : ORG_B;
      await expect(page.getByTestId("org-name")).toHaveText(org.name);
      const text = await page.locator("main").innerText();
      const foreignOnly = org === ORG_A ? [ONLY_B_CUSTOMER, ONLY_B_ITEM] : [ONLY_A_CUSTOMER, ONLY_A_ITEM];
      for (const name of foreignOnly) expect(text).not.toContain(name);
    }
    expect(trail.at(-1)).toContain(`/o/${ORG_A.id}/customers`);
    expect((await customerNames(page)).sort()).toEqual([ONLY_A_CUSTOMER, TWIN_CUSTOMER]);

    for (let step = 0; step < 3; step += 1) await page.goForward();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    expect((await customerNames(page)).sort()).toEqual([ONLY_B_CUSTOMER, TWIN_CUSTOMER]);
  });

  test("going back to a form of organization A after visiting B never shows B's data in A's form", async ({ page }) => {
    await page.goto(`${catalog(ORG_A.id)}/${ids.a.item}`);
    await expect(page.getByLabel("Price excluding VAT", { exact: true })).toHaveValue("11.11");
    await switchTo(page, ORG_B);
    await page.goto(`${catalog(ORG_B.id)}/${ids.b.item}`);
    await expect(page.getByLabel("Price excluding VAT", { exact: true })).toHaveValue("22.22");

    await page.goBack();
    await page.goBack();

    await expect(page).toHaveURL(`${catalog(ORG_A.id)}/${ids.a.item}`);
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);
    await expect(page.getByLabel("Price excluding VAT", { exact: true })).toHaveValue("11.11");
  });
});

test.describe("forged headers are ineffective on these workflows", () => {
  const FORGED = { "x-organization-id": ORG_B.id, "x-dev-user-email": MARIA, "x-forwarded-for": "10.0.0.1", authorization: "Bearer forged" };

  test("a forged organization header cannot move a read or a write to another organization", async ({ context }) => {
    const list = await context.request.get(bffUrl(ORG_A.id, `/customers?q=${encodeURIComponent(TAG)}`), { headers: FORGED });
    const names = ((await list.json()) as { name: string }[]).map((customer) => customer.name).sort();
    expect(names).toEqual([ONLY_A_CUSTOMER, TWIN_CUSTOMER]);

    const name = unique("Forged Create");
    const customer = await context.request.post(bffUrl(ORG_A.id, "/customers"), { headers: FORGED, data: { customer_type: "person", name } });
    const item = await context.request.post(bffUrl(ORG_A.id, "/items"), { headers: FORGED, data: { type: "service", name, unit: "h", price_ex_vat: "1.00", vat_rate: "25" } });
    expect([customer.status(), item.status()]).toEqual([201, 201]);
    expect(testRow(`select organization_id from customers where name = ${sql(name)}`)).toBe(ORG_A.id);
    expect(testRow(`select organization_id from items where name = ${sql(name)}`)).toBe(ORG_A.id);
  });

  test("a forged identity cannot make someone else's records writable", async ({ context }) => {
    await context.clearCookies();
    await signIn(context, MARIA); // B only
    const forgedAsFredrik = { ...FORGED, "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id };
    const name = unique("Forged Identity");

    const read = await context.request.get(bffUrl(ORG_A.id, "/items"), { headers: forgedAsFredrik });
    const write = await context.request.post(bffUrl(ORG_A.id, "/items"), { headers: forgedAsFredrik, data: { type: "service", name, unit: "h", price_ex_vat: "1.00", vat_rate: "25" } });
    const edit = await context.request.patch(bffUrl(ORG_A.id, `/customers/${ids.a.customer}`), { headers: forgedAsFredrik, data: { name: "HACKED" } });

    expect([read.status(), write.status(), edit.status()]).toEqual([404, 404, 404]);
    expect(testRow(`select count(*) from items where name = ${sql(name)}`)).toBe("0");
    expect(testRow(`select name from customers where id = ${sql(ids.a.customer)}`)).toBe(TWIN_CUSTOMER);
  });

  test("without the session cookie, forged headers get nothing", async ({ request }) => {
    const headers = { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id };
    expect((await request.get(bffUrl(ORG_A.id, "/customers"), { headers })).status()).toBe(401);
    expect((await request.get(bffUrl(ORG_A.id, "/items"), { headers })).status()).toBe(401);
    expect((await request.patch(bffUrl(ORG_A.id, `/items/${ids.a.item}`), { headers, data: { name: "HACKED" } })).status()).toBe(401);
    expect(testRow(`select name from items where id = ${sql(ids.a.item)}`)).toBe(TWIN_ITEM);
  });
});
