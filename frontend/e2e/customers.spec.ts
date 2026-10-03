import { expect, test, type Page } from "./fixtures";

import { FREDRIK, ORG_A, bffUrl, createCustomer, signIn, sql, testRow, unique } from "./support";

/**
 * The Customers workflows in a real browser against the real stack and the dedicated test
 * database. Every test creates its own uniquely named records, so the specs can share one
 * seeded database in any order.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const list = `/o/${ORG_A.id}/customers`;
const names = (page: Page) => page.getByTestId("customer-row").locator("td:first-child").allTextContents();

test.describe("create", () => {
  test("a customer entered in the browser persists in PostgreSQL and is listed after a reload", async ({ page }) => {
    const name = unique("Created Customer");

    await page.goto(`${list}/new`);
    await page.getByLabel("Type", { exact: true }).selectOption("company");
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByLabel("Email", { exact: true }).fill("office@example.test");
    await page.getByLabel("Phone", { exact: true }).fill("070-123 45 67");
    await page.getByTestId("submit").click();

    await expect(page).toHaveURL(new RegExp(`${list}/[0-9a-f-]{36}\\?created=1$`));
    await expect(page.getByTestId("created")).toBeVisible();
    await expect(page.getByTestId("record-name")).toHaveText(name);

    // PostgreSQL has it, in the organization from the URL.
    expect(testRow(`select organization_id || '|' || customer_type || '|' || email || '|' || phone || '|' || active from customers where name = ${sql(name)}`)).toBe(
      `${ORG_A.id}|company|office@example.test|070-123 45 67|true`,
    );

    await page.reload();
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue(name);
    await expect(page.getByLabel("Type", { exact: true })).toHaveValue("company");

    await page.goto(`${list}?q=${encodeURIComponent(name)}`);
    await expect(page.getByTestId("customer-row")).toHaveCount(1);
    await expect(page.getByTestId("customer-row")).toContainText("office@example.test");
  });

  test("a blank email and phone are stored as no value, not as empty text", async ({ page }) => {
    const name = unique("Blank Optional");

    await page.goto(`${list}/new`);
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("record-name")).toHaveText(name);

    expect(testRow(`select (email is null) || '|' || (phone is null) from customers where name = ${sql(name)}`)).toBe("true|true");
  });

  test("can be created inactive", async ({ page }) => {
    const name = unique("Created Inactive");

    await page.goto(`${list}/new`);
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByLabel("Active", { exact: true }).uncheck();
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("status")).toHaveText("Inactive");
    expect(testRow(`select active::text from customers where name = ${sql(name)}`)).toBe("false");
  });

  test("the backend's validation answers appear on the right controls and nothing is created", async ({ page }) => {
    const before = testRow(`select count(*) from customers where organization_id = ${sql(ORG_A.id)}`);

    await page.goto(`${list}/new`);
    await page.getByLabel("Email", { exact: true }).fill("x".repeat(321)); // name stays empty
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-name")).toBeVisible();
    await expect(page.getByTestId("error-email")).toBeVisible();
    await expect(page.getByLabel("Name", { exact: true })).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByLabel("Phone", { exact: true })).toHaveAttribute("aria-invalid", "false");
    await expect(page.getByTestId("form-error")).toHaveCount(0);
    await expect(page).toHaveURL(`${list}/new`);
    expect(testRow(`select count(*) from customers where organization_id = ${sql(ORG_A.id)}`)).toBe(before);

    // Fixing the input and sending again works, and the error is gone.
    await page.getByLabel("Name", { exact: true }).fill(unique("Fixed After Error"));
    await page.getByLabel("Email", { exact: true }).fill("ok@example.test");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();
  });

  test("a name that is too long is refused by the backend on the name control", async ({ page }) => {
    await page.goto(`${list}/new`);
    await page.getByLabel("Name", { exact: true }).fill("N".repeat(256));
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("error-name")).toContainText("255");
  });

  test("pressing the button twice quickly creates one customer", async ({ page }) => {
    const name = unique("Double Click");

    await page.goto(`${list}/new`);
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByTestId("submit").dblclick();
    await expect(page.getByTestId("record-name")).toHaveText(name);

    expect(testRow(`select count(*) from customers where name = ${sql(name)}`)).toBe("1");
  });
});

test.describe("edit", () => {
  test("changes persist, the page title follows, and untouched fields stay as they were", async ({ page, context }) => {
    const name = unique("Before Edit");
    const created = await createCustomer(context, ORG_A.id, name);
    await context.request.patch(bffUrl(ORG_A.id, `/customers/${created.id}`), { data: { email: "keep@example.test" } });

    await page.goto(`${list}/${created.id}`);
    const renamed = unique("After Edit");
    await page.getByLabel("Name", { exact: true }).fill(renamed);
    await page.getByLabel("Phone", { exact: true }).fill("0900-1");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("saved")).toBeVisible();
    await expect(page.getByTestId("record-name")).toHaveText(renamed); // server component re-rendered
    expect(testRow(`select name || '|' || email || '|' || phone from customers where id = ${sql(created.id)}`)).toBe(`${renamed}|keep@example.test|0900-1`);

    await page.reload();
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue(renamed);
  });

  test("clearing the email stores no value", async ({ page, context }) => {
    const created = await createCustomer(context, ORG_A.id, unique("Clear Email"));
    await context.request.patch(bffUrl(ORG_A.id, `/customers/${created.id}`), { data: { email: "gone@example.test" } });

    await page.goto(`${list}/${created.id}`);
    await page.getByLabel("Email", { exact: true }).fill("");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();

    expect(testRow(`select (email is null)::text from customers where id = ${sql(created.id)}`)).toBe("true");
  });

  test("a validation error keeps the draft, shows on the control, and the stored record is unchanged", async ({ page, context }) => {
    const name = unique("Keep Me");
    const created = await createCustomer(context, ORG_A.id, name);

    await page.goto(`${list}/${created.id}`);
    await page.getByLabel("Name", { exact: true }).fill("");
    await page.getByLabel("Phone", { exact: true }).fill("draft-phone");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-name")).toBeVisible();
    await expect(page.getByLabel("Phone", { exact: true })).toHaveValue("draft-phone");
    expect(testRow(`select name from customers where id = ${sql(created.id)}`)).toBe(name);
  });

  test("saving without changes says so and sends nothing", async ({ page, context }) => {
    const created = await createCustomer(context, ORG_A.id, unique("No Change"));
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") writes.push(`${request.method()} ${request.url()}`);
    });

    await page.goto(`${list}/${created.id}`);
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("unchanged")).toBeVisible();
    expect(writes).toEqual([]);
  });

  test("the list shows the saved change after the Back link and after browser Back", async ({ page, context }) => {
    const created = await createCustomer(context, ORG_A.id, unique("History Before"));
    const renamed = unique("History After");

    await page.goto(`${list}?q=History`);
    await page.getByRole("link", { name: created.name }).click();
    await page.getByLabel("Name", { exact: true }).fill(renamed);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();

    await page.goBack(); // browser Back to the list visited before the edit
    await expect(page).toHaveURL(`${list}?q=History`);
    await expect(page.getByRole("link", { name: renamed })).toBeVisible();
    await expect(page.getByRole("link", { name: created.name })).toHaveCount(0);
  });
});

test.describe("history after a create", () => {
  test("browser Back to a list visited before the create shows the new customer", async ({ page }) => {
    const name = unique("Back After Create");
    const query = `?q=${encodeURIComponent("Back After Create")}`;

    await page.goto(`${list}${query}`); // visited (and possibly cached by the router) before the create
    await page.getByTestId("new-customer").click();
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();

    await page.goBack(); // the form
    await page.goBack(); // the list

    await expect(page).toHaveURL(`${list}${query}`);
    await expect(page.getByRole("link", { name })).toBeVisible();
  });
});

test.describe("deactivate and reactivate", () => {
  test("is one click each, persists, and the list filter follows", async ({ page, context }) => {
    const name = unique("Toggle Me");
    const created = await createCustomer(context, ORG_A.id, name);

    await page.goto(`${list}/${created.id}`);
    await expect(page.getByTestId("status")).toHaveText("Active");
    await page.getByRole("button", { name: "Deactivate customer" }).click();
    await expect(page.getByTestId("status")).toHaveText("Inactive");
    expect(testRow(`select active::text from customers where id = ${sql(created.id)}`)).toBe("false");

    await page.reload();
    await expect(page.getByTestId("status")).toHaveText("Inactive");

    await page.goto(`${list}?q=${encodeURIComponent(name)}&active=active`);
    await expect(page.getByTestId("empty")).toBeVisible();
    await page.goto(`${list}?q=${encodeURIComponent(name)}&active=inactive`);
    await expect(page.getByTestId("customer-row")).toHaveCount(1);

    await page.getByRole("link", { name }).click();
    await page.getByRole("button", { name: "Reactivate customer" }).click();
    await expect(page.getByTestId("status")).toHaveText("Active");
    expect(testRow(`select active::text from customers where id = ${sql(created.id)}`)).toBe("true");
  });

  test("does not touch unsaved edits in the form", async ({ page, context }) => {
    const created = await createCustomer(context, ORG_A.id, unique("Toggle Draft"));

    await page.goto(`${list}/${created.id}`);
    await page.getByLabel("Phone", { exact: true }).fill("not saved yet");
    await page.getByRole("button", { name: "Deactivate customer" }).click();
    await expect(page.getByTestId("status")).toHaveText("Inactive");

    await expect(page.getByLabel("Phone", { exact: true })).toHaveValue("not saved yet");
    expect(testRow(`select (phone is null)::text from customers where id = ${sql(created.id)}`)).toBe("true");
  });
});

test.describe("list, search and filters", () => {
  test("search is a partial, case-insensitive match and filters combine with it", async ({ page, context }) => {
    const prefix = unique("Filt");
    const active = await createCustomer(context, ORG_A.id, `${prefix} Alpha`);
    const inactive = await createCustomer(context, ORG_A.id, `${prefix} Beta`);
    await createCustomer(context, ORG_A.id, `${prefix} Gamma`);
    await context.request.patch(bffUrl(ORG_A.id, `/customers/${inactive.id}`), { data: { active: false } });

    await page.goto(`${list}?q=${encodeURIComponent(prefix.toLowerCase())}`);
    expect((await names(page)).sort()).toEqual([`${prefix} Alpha`, `${prefix} Beta`, `${prefix} Gamma`]);

    await page.goto(`${list}?q=${encodeURIComponent(prefix)}&active=active`);
    expect((await names(page)).sort()).toEqual([`${prefix} Alpha`, `${prefix} Gamma`]);

    await page.goto(`${list}?q=${encodeURIComponent(prefix)}&active=inactive`);
    expect(await names(page)).toEqual([`${prefix} Beta`]);

    await page.goto(`${list}?q=${encodeURIComponent(prefix + " alp")}`);
    expect(await names(page)).toEqual([`${prefix} Alpha`]);
    expect(active.id).toBeTruthy();
  });

  test("the filter form puts the filters in the address and keeps them in its controls", async ({ page, context }) => {
    const prefix = unique("Form");
    const one = await createCustomer(context, ORG_A.id, `${prefix} One`);
    const two = await createCustomer(context, ORG_A.id, `${prefix} Two`);
    await context.request.patch(bffUrl(ORG_A.id, `/customers/${two.id}`), { data: { active: false } });

    await page.goto(list);
    await page.getByRole("search").getByLabel("Search").fill(prefix);
    await page.getByRole("search").getByLabel("Status").selectOption("inactive");
    await page.getByRole("button", { name: "Apply" }).click();

    await expect(page).toHaveURL(`${list}?q=${encodeURIComponent(prefix).replaceAll("%20", "+")}&active=inactive`);
    expect(await names(page)).toEqual([`${prefix} Two`]);
    await expect(page.getByRole("search").getByLabel("Search")).toHaveValue(prefix);
    await expect(page.getByRole("search").getByLabel("Status")).toHaveValue("inactive");

    await page.getByRole("link", { name: "Clear" }).click();
    await expect(page).toHaveURL(list);
    await expect(page.getByRole("search").getByLabel("Search")).toHaveValue("");
    expect(one.id).toBeTruthy();
  });

  test("percent and underscore in a search are plain characters, not wildcards", async ({ page, context }) => {
    const prefix = unique("Wild");
    await createCustomer(context, ORG_A.id, `${prefix} 100% sure`);
    await createCustomer(context, ORG_A.id, `${prefix} 100 sure`);

    await page.goto(`${list}?q=${encodeURIComponent(prefix + " 100%")}`);

    expect(await names(page)).toEqual([`${prefix} 100% sure`]);
  });

  test("says so when nothing matches", async ({ page }) => {
    await page.goto(`${list}?q=${encodeURIComponent("no such customer " + Date.now())}`);
    await expect(page.getByTestId("empty")).toHaveText("No customers match.");
  });

  test("a hostile or malformed address is ignored, not forwarded", async ({ page }) => {
    const response = await page.goto(`${list}?page=-3&active=maybe&q=%27%3B%20drop%20table%20customers%3B--&limit=100000&offset=5&organization_id=${ORG_A.id}`);
    expect(response?.status()).toBe(200);
    await expect(page.getByTestId("empty")).toBeVisible();
    expect(testRow("select (count(*) > 0)::text from customers")).toBe("true");
  });

  test("pages through more than one page of customers", async ({ page, context }) => {
    const prefix = unique("Page");
    for (let index = 0; index < 26; index += 1) {
      await createCustomer(context, ORG_A.id, `${prefix} ${String(index).padStart(2, "0")}`);
    }

    await page.goto(`${list}?q=${encodeURIComponent(prefix)}`);
    await expect(page.getByTestId("customer-row")).toHaveCount(25);
    await expect(page.getByTestId("page-number")).toHaveText("Page 1");

    await page.getByRole("link", { name: "Next" }).click();
    await expect(page).toHaveURL(/page=2/);
    expect(await names(page)).toEqual([`${prefix} 25`]);
    await expect(page.getByTestId("page-number")).toHaveText("Page 2");
    await expect(page.getByRole("link", { name: "Next" })).toHaveCount(0);

    await page.getByRole("link", { name: "Previous" }).click();
    await expect(page.getByTestId("customer-row")).toHaveCount(25);
  });
});

test.describe("navigation", () => {
  test("the main navigation reaches Customers and the list links to the form and the record", async ({ page, context }) => {
    const created = await createCustomer(context, ORG_A.id, unique("Nav Customer"));

    await page.goto(`/o/${ORG_A.id}`);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Customers" }).click();
    await expect(page).toHaveURL(list);

    await page.getByTestId("new-customer").click();
    await expect(page).toHaveURL(`${list}/new`);
    await page.getByRole("link", { name: "Cancel" }).click();
    await expect(page).toHaveURL(list);

    await page.goto(`${list}?q=${encodeURIComponent(created.name)}`);
    await page.getByRole("link", { name: created.name }).click();
    await expect(page).toHaveURL(`${list}/${created.id}`);
  });
});
