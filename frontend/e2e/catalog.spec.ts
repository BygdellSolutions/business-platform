import { expect, test, type Page } from "./fixtures";

import { BACKEND_URL } from "./env";
import { FREDRIK, ORG_A, bffUrl, createItem, signIn, sql, testRow, unique } from "./support";

/**
 * The Catalog workflows in a real browser against the real stack and the dedicated test
 * database, with the money rules as the headline: a price typed as "8.20" must be "8.20" in
 * the form, in the request, in FastAPI's answer, in PostgreSQL, and on screen again.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const list = `/o/${ORG_A.id}/catalog`;
const names = (page: Page) => page.getByTestId("item-row").locator("td:first-child").allTextContents();

const price = (page: Page) => page.getByLabel("Price excluding VAT", { exact: true });
const vat = (page: Page) => page.getByLabel("VAT rate (%)", { exact: true });

async function fillItem(page: Page, fields: { name: string; unit?: string; price: string; vat: string; type?: "service" | "product" }) {
  await page.getByLabel("Type", { exact: true }).selectOption(fields.type ?? "service");
  await page.getByLabel("Name", { exact: true }).fill(fields.name);
  await page.getByLabel("Unit", { exact: true }).fill(fields.unit ?? "piece");
  await price(page).fill(fields.price);
  await vat(page).fill(fields.vat);
}

const isCreate = (url: string, method: string) => method === "POST" && url.endsWith(`/api/o/${ORG_A.id}/items`);

test.describe("decimal values survive the whole round trip unchanged", () => {
  // Typed value, what the backend answers (two decimals, as stored), and a VAT that is valid for it.
  const CASES = [
    { price: "0.10", vat: "0.10", vatOut: "0.10" },
    { price: "4.35", vat: "4.35", vatOut: "4.35" },
    { price: "8.20", vat: "8.20", vatOut: "8.20" },
    { price: "9999999999.99", vat: "100", vatOut: "100.00" },
    { price: "1.15", vat: "6.5", vatOut: "6.50" },
    { price: "0.30", vat: "25", vatOut: "25.00" },
  ];

  for (const value of CASES) {
    test(`price ${value.price} and VAT ${value.vat}: form → BFF → FastAPI → PostgreSQL → screen`, async ({ page, context }) => {
      const name = unique("Round Trip");
      await page.goto(`${list}/new`);
      await fillItem(page, { name, price: value.price, vat: value.vat });

      const request = page.waitForRequest((r) => isCreate(r.url(), r.method()));
      const response = page.waitForResponse((r) => isCreate(r.url(), r.request().method()));
      await page.getByTestId("submit").click();

      // 1. The browser sent quoted strings, exactly as typed (never a JSON number).
      const sent = (await request).postData() ?? "";
      expect(sent).toContain(`"price_ex_vat":"${value.price}"`);
      expect(sent).toContain(`"vat_rate":"${value.vat}"`);
      expect(sent).not.toMatch(/"price_ex_vat":\s*[0-9]/);
      expect(sent).not.toContain("organization");

      // 2. The BFF handed back the backend's own text: strings with two decimals.
      const received = await (await response).text();
      expect(received).toMatch(new RegExp(`"price_ex_vat":\\s*"${value.price}"`));
      expect(received).toMatch(new RegExp(`"vat_rate":\\s*"${value.vatOut}"`));

      await expect(page).toHaveURL(new RegExp(`${list}/[0-9a-f-]{36}\\?created=1$`));
      const id = page.url().match(/catalog\/([0-9a-f-]{36})/)![1];

      // 3. PostgreSQL stores exactly that value.
      expect(testRow(`select price_ex_vat::text || '|' || vat_rate::text from items where id = ${sql(id)}`)).toBe(`${value.price}|${value.vatOut}`);

      // 4. FastAPI itself answers with strings (asked directly, bypassing the frontend).
      const direct = await context.request.get(`${BACKEND_URL}/api/items/${id}`, { headers: { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id } });
      const directText = await direct.text();
      expect(directText).toMatch(new RegExp(`"price_ex_vat":\\s*"${value.price}"`));
      expect(directText).toMatch(new RegExp(`"vat_rate":\\s*"${value.vatOut}"`));

      // 5. The page shows it, in the form (server-rendered) and in the list, unformatted.
      await expect(price(page)).toHaveValue(value.price);
      await expect(vat(page)).toHaveValue(value.vatOut);
      await page.goto(`${list}?q=${encodeURIComponent(name)}`);
      await expect(page.getByTestId("item-price")).toHaveText(value.price);
      await expect(page.getByTestId("item-vat")).toHaveText(value.vatOut);

      // 6. And again after a reload, straight from the database.
      await page.reload();
      await expect(page.getByTestId("item-price")).toHaveText(value.price);
    });
  }

  test("editing a price keeps the exact digits, and the form then shows the backend's formatting", async ({ page, context }) => {
    const created = await createItem(context, ORG_A.id, { name: unique("Edit Price"), price_ex_vat: "850.00", vat_rate: "25" });

    await page.goto(`${list}/${created.id}`);
    await expect(price(page)).toHaveValue("850.00");
    await price(page).fill("8.2");
    await vat(page).fill("0.1");
    const request = page.waitForRequest((r) => r.method() === "PATCH");
    await page.getByTestId("submit").click();

    const sent = (await request).postData() ?? "";
    expect(sent).toContain('"price_ex_vat":"8.2"');
    expect(sent).toContain('"vat_rate":"0.1"');
    await expect(page.getByTestId("saved")).toBeVisible();
    await expect(price(page)).toHaveValue("8.20");
    await expect(vat(page)).toHaveValue("0.10");
    expect(testRow(`select price_ex_vat::text || '|' || vat_rate::text from items where id = ${sql(created.id)}`)).toBe("8.20|0.10");
  });

  test("the seeded 850.00 / 25.00 service is listed exactly like that", async ({ page }) => {
    await page.goto(`${list}?q=Horse massage`);
    await expect(page.getByTestId("item-price")).toHaveText("850.00");
    await expect(page.getByTestId("item-vat")).toHaveText("25.00");
    await expect(page.getByTestId("item-price-inc-vat")).toHaveText("1062.50"); // computed by the backend
  });

  test("a running promotion shows the base price, the promotion and the promotion price, each incl. VAT too", async ({ page, context }) => {
    const created = await createItem(context, ORG_A.id, { name: unique("Promo"), price_ex_vat: "1000.00", vat_rate: "25" });
    // A day before of today in UTC, so the organization's own "today" is inside the open-ended period whatever its zone.
    const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
    const response = await context.request.post(bffUrl(ORG_A.id, `/items/${created.id}/discounts`), { data: { percent: "20", starts_on: yesterday } });
    expect(response.status()).toBe(201);

    await page.goto(`${list}?q=${encodeURIComponent(created.name)}`);
    await expect(page.getByTestId("item-price")).toHaveText("1000.00");
    await expect(page.getByTestId("current-discount")).toContainText("20.00 %");
    await expect(page.getByTestId("item-promotion-price")).toHaveText("800.00");
    await expect(page.getByTestId("item-price-inc-vat")).toHaveText("1000.00");
  });

  test("a price can be entered incl. VAT and is stored excl. VAT", async ({ page }) => {
    const name = unique("Inc VAT");
    await page.goto(`${list}/new`);
    await page.getByLabel("Name").fill(name);
    await page.getByLabel("Unit").fill("hour");
    await page.getByLabel("Price is entered").selectOption("inc");
    await page.getByLabel("Price including VAT").fill("1000");
    await page.getByLabel("VAT rate (%)").fill("25");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();
    expect(testRow(`select price_ex_vat::text from items where name = ${sql(name)}`)).toBe("800.00");
  });
});

test.describe("create", () => {
  test("an item entered in the browser persists in PostgreSQL with all its fields", async ({ page }) => {
    const name = unique("Saddle Fitting");

    await page.goto(`${list}/new`);
    await page.getByLabel("Type", { exact: true }).selectOption("product");
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByLabel("Description", { exact: true }).fill("Massage för häst – 60 min");
    await page.getByLabel("Unit", { exact: true }).fill("kg");
    await price(page).fill("19.99");
    await vat(page).fill("12");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("created")).toBeVisible();
    await expect(page.getByTestId("record-name")).toHaveText(name);
    expect(testRow(`select organization_id || '|' || type || '|' || description || '|' || unit || '|' || price_ex_vat::text || '|' || vat_rate::text || '|' || active from items where name = ${sql(name)}`)).toBe(
      `${ORG_A.id}|product|Massage för häst – 60 min|kg|19.99|12.00|true`,
    );
  });

  test("a blank description is stored as no value", async ({ page }) => {
    const name = unique("No Description");
    await page.goto(`${list}/new`);
    await fillItem(page, { name, price: "1", vat: "25" });
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();

    expect(testRow(`select (description is null)::text from items where name = ${sql(name)}`)).toBe("true");
  });

  test("can be created inactive", async ({ page }) => {
    const name = unique("Item Inactive");
    await page.goto(`${list}/new`);
    await fillItem(page, { name, price: "1", vat: "25" });
    await page.getByLabel("Active", { exact: true }).uncheck();
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("status")).toHaveText("Inactive");
    expect(testRow(`select active::text from items where name = ${sql(name)}`)).toBe("false");
  });

  test("what the backend rejects is shown on the right control, and nothing is stored", async ({ page }) => {
    const rejected = [
      { price: "10000000000.00", vat: "25", control: "error-price_ex_vat" }, // more digits than the column holds
      { price: "1.005", vat: "25", control: "error-price_ex_vat" }, // three decimals
      { price: "10", vat: "100.01", control: "error-vat_rate" }, // above 100
      { price: "10", vat: "999", control: "error-vat_rate" },
    ];
    await page.goto(`${list}/new`);

    for (const attempt of rejected) {
      const name = unique("Rejected");
      await fillItem(page, { name, price: attempt.price, vat: attempt.vat });
      await page.getByTestId("submit").click();

      await expect(page.getByTestId(attempt.control)).toBeVisible();
      await expect(page).toHaveURL(`${list}/new`);
      expect(testRow(`select count(*) from items where name = ${sql(name)}`)).toBe("0");
      expect(await price(page).inputValue()).toBe(attempt.price); // the draft is untouched
    }
  });

  test("required fields the backend checks show on their controls", async ({ page }) => {
    await page.goto(`${list}/new`);
    await price(page).fill("10");
    await vat(page).fill("25");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-name")).toBeVisible();
    await expect(page.getByTestId("error-unit")).toBeVisible();
    await expect(page.getByTestId("error-price_ex_vat")).toHaveCount(0);
  });

  test("something that is not a decimal at all is stopped before any request, with a message on the control", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST") posts.push(request.url());
    });
    await page.goto(`${list}/new`);

    for (const typed of ["1,5", "abc", "-1", "1e2", ""]) {
      await fillItem(page, { name: unique("Shape"), price: typed, vat: "25" });
      await page.getByTestId("submit").click();
      await expect(page.getByTestId("error-price_ex_vat")).toContainText("Enter a number such as 850.00");
    }

    expect(posts).toEqual([]);
  });
});

test.describe("edit, deactivate and reactivate", () => {
  test("changes persist and untouched fields stay as they were", async ({ page, context }) => {
    const created = await createItem(context, ORG_A.id, { name: unique("Item Before"), unit: "hour", price_ex_vat: "100.00", vat_rate: "25", description: "Keep this" });
    const renamed = unique("Item After");

    await page.goto(`${list}/${created.id}`);
    await page.getByLabel("Name", { exact: true }).fill(renamed);
    await page.getByLabel("Type", { exact: true }).selectOption("product");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("saved")).toBeVisible();
    await expect(page.getByTestId("record-name")).toHaveText(renamed);
    expect(testRow(`select name || '|' || type || '|' || description || '|' || unit || '|' || price_ex_vat::text from items where id = ${sql(created.id)}`)).toBe(`${renamed}|product|Keep this|hour|100.00`);
  });

  test("a rejected edit keeps the draft and leaves the stored item alone", async ({ page, context }) => {
    const created = await createItem(context, ORG_A.id, { name: unique("Stays"), price_ex_vat: "100.00", vat_rate: "25" });

    await page.goto(`${list}/${created.id}`);
    await price(page).fill("123456789012.34");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-price_ex_vat")).toBeVisible();
    await expect(price(page)).toHaveValue("123456789012.34");
    expect(testRow(`select price_ex_vat::text from items where id = ${sql(created.id)}`)).toBe("100.00");
  });

  test("deactivating and reactivating persists and the list filter follows", async ({ page, context }) => {
    const name = unique("Item Toggle");
    const created = await createItem(context, ORG_A.id, { name });

    await page.goto(`${list}/${created.id}`);
    await page.getByRole("button", { name: "Deactivate item" }).click();
    await expect(page.getByTestId("status")).toHaveText("Inactive");
    expect(testRow(`select active::text from items where id = ${sql(created.id)}`)).toBe("false");

    await page.goto(`${list}?q=${encodeURIComponent(name)}&active=active`);
    await expect(page.getByTestId("empty")).toBeVisible();
    await page.goto(`${list}?q=${encodeURIComponent(name)}&active=inactive`);
    await expect(page.getByTestId("item-row")).toHaveCount(1);

    await page.getByRole("link", { name }).click();
    await page.getByRole("button", { name: "Reactivate item" }).click();
    await expect(page.getByTestId("status")).toHaveText("Active");
    expect(testRow(`select active::text from items where id = ${sql(created.id)}`)).toBe("true");
  });

  test("the price stays exactly as stored after deactivating (the toggle sends only the status)", async ({ page, context }) => {
    const created = await createItem(context, ORG_A.id, { name: unique("Toggle Price"), price_ex_vat: "0.10", vat_rate: "8.2" });
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "PATCH") writes.push(request.postData() ?? "");
    });

    await page.goto(`${list}/${created.id}`);
    await page.getByRole("button", { name: "Deactivate item" }).click();
    await expect(page.getByTestId("status")).toHaveText("Inactive");

    expect(writes).toEqual(['{"active":false}']);
    expect(testRow(`select price_ex_vat::text || '|' || vat_rate::text from items where id = ${sql(created.id)}`)).toBe("0.10|8.20");
  });
});

test.describe("list, search and filters", () => {
  test("type, status and search combine, and the filter form keeps them in the address", async ({ page, context }) => {
    const prefix = unique("CatFilt");
    await createItem(context, ORG_A.id, { name: `${prefix} Massage`, type: "service" });
    await createItem(context, ORG_A.id, { name: `${prefix} Saddle`, type: "product" });
    const old = await createItem(context, ORG_A.id, { name: `${prefix} Old saddle`, type: "product" });
    await context.request.patch(bffUrl(ORG_A.id, `/items/${old.id}`), { data: { active: false } });

    await page.goto(`${list}?q=${encodeURIComponent(prefix)}`);
    expect((await names(page)).sort()).toEqual([`${prefix} Massage`, `${prefix} Old saddle`, `${prefix} Saddle`]);

    await page.goto(`${list}?q=${encodeURIComponent(prefix)}&type=product`);
    expect((await names(page)).sort()).toEqual([`${prefix} Old saddle`, `${prefix} Saddle`]);

    await page.goto(`${list}?q=${encodeURIComponent(prefix)}&type=product&active=active`);
    expect(await names(page)).toEqual([`${prefix} Saddle`]);

    await page.goto(list);
    await page.getByRole("search").getByLabel("Search").fill(prefix);
    await page.getByRole("search").getByLabel("Type").selectOption("service");
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page).toHaveURL(/type=service/);
    expect(await names(page)).toEqual([`${prefix} Massage`]);
    await expect(page.getByRole("search").getByLabel("Type")).toHaveValue("service");
  });

  test("search also matches the description", async ({ page, context }) => {
    const token = unique("descword").replace(" ", "");
    const created = await createItem(context, ORG_A.id, { name: unique("Described"), description: `contains ${token} inside` });

    await page.goto(`${list}?q=${encodeURIComponent(token)}`);

    expect(await names(page)).toEqual([created.name]);
  });

  test("an unknown type in the address is ignored", async ({ page }) => {
    const response = await page.goto(`${list}?type=horse&active=maybe&page=0`);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("search").getByLabel("Type")).toHaveValue("");
  });
});

test.describe("history", () => {
  test("browser Back to a list visited before a create or an edit shows the saved data", async ({ page }) => {
    const name = unique("Back Item");
    const renamed = `${name} renamed`;
    const query = `?q=${encodeURIComponent("Back Item")}`;

    await page.goto(`${list}${query}`);
    await page.getByTestId("new-item").click();
    await fillItem(page, { name, price: "8.20", vat: "25" });
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();
    await page.goBack();
    await page.goBack();
    await expect(page).toHaveURL(`${list}${query}`);
    await expect(page.getByRole("link", { name })).toBeVisible();
    await expect(page.getByTestId("item-price")).toHaveText("8.20");

    await page.getByRole("link", { name }).click();
    await page.getByLabel("Name", { exact: true }).fill(renamed);
    await price(page).fill("9.30");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("link", { name: renamed })).toBeVisible();
    await expect(page.getByTestId("item-price")).toHaveText("9.30");
  });
});

test.describe("navigation", () => {
  test("the main navigation reaches Catalog and the list links to the form and the record", async ({ page, context }) => {
    const created = await createItem(context, ORG_A.id, { name: unique("Nav Item") });

    await page.goto(`/o/${ORG_A.id}`);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Catalog" }).click();
    await expect(page).toHaveURL(list);

    await page.getByTestId("new-item").click();
    await expect(page).toHaveURL(`${list}/new`);
    await page.getByRole("link", { name: "Cancel" }).click();
    await expect(page).toHaveURL(list);

    await page.goto(`${list}?q=${encodeURIComponent(created.name)}`);
    await page.getByRole("link", { name: created.name }).click();
    await expect(page).toHaveURL(`${list}/${created.id}`);
  });
});
