import { expect, test } from "./fixtures";

import { FREDRIK, ORG_A, ORG_B, bffUrl, signIn, sql, testRow, unique } from "./support";

/**
 * The customer billing profile in a real browser: address, country code, registration and VAT
 * number are saved to PostgreSQL, come back after a reload, are refused by the backend when the
 * country code is malformed, and never cross into another organization's customer of the same name.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const list = `/o/${ORG_A.id}/customers`;
const PROFILE = {
  "Address line 1": "Ridvägen 2",
  "Address line 2": "Box 17",
  "Postal code": "903 30",
  City: "Umeå",
  "Country code": "SE",
  "Registration number": "802000-0001",
  "VAT number": "SE802000000101",
};

test("a customer is created with a billing profile, which persists and survives a reload", async ({ page }) => {
  const name = unique("Profile Customer");

  await page.goto(`${list}/new`);
  await page.getByLabel("Type", { exact: true }).selectOption("company");
  await page.getByLabel("Name", { exact: true }).fill(name);
  for (const [label, value] of Object.entries(PROFILE)) await page.getByLabel(label, { exact: true }).fill(label === "Country code" ? value.toLowerCase() : value);
  await page.getByTestId("submit").click();

  await expect(page.getByTestId("created")).toBeVisible();
  expect(
    testRow(
      `select organization_id || '|' || address_line1 || '|' || address_line2 || '|' || postal_code || '|' || city || '|' || country_code || '|' || registration_number || '|' || vat_number from customers where name = ${sql(name)}`,
    ),
  ).toBe(`${ORG_A.id}|Ridvägen 2|Box 17|903 30|Umeå|SE|802000-0001|SE802000000101`);

  await page.reload();
  for (const [label, value] of Object.entries(PROFILE)) await expect(page.getByLabel(label, { exact: true })).toHaveValue(value);
});

test("a customer without a profile stores no values, and a field can be cleared again", async ({ page }) => {
  const name = unique("Cleared Profile");
  await page.goto(`${list}/new`);
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("City", { exact: true }).fill("Umeå");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("created")).toBeVisible();
  expect(testRow(`select city || '|' || (vat_number is null) || '|' || (address_line1 is null) from customers where name = ${sql(name)}`)).toBe("Umeå|true|true");

  await page.getByLabel("City", { exact: true }).fill("");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();
  expect(testRow(`select city is null from customers where name = ${sql(name)}`)).toBe("t");
});

test("a malformed country code is refused at its box and nothing is saved", async ({ page }) => {
  const name = unique("Bad Country");
  await page.goto(`${list}/new`);
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Country code", { exact: true }).fill("SWE");
  await page.getByTestId("submit").click();

  await expect(page.getByTestId("error-country_code")).toBeVisible();
  await expect(page.getByLabel("Country code", { exact: true })).toHaveValue("SWE");
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(name);
  expect(testRow(`select count(*) from customers where name = ${sql(name)}`)).toBe("0");
});

test("editing a profile saves only the changed field", async ({ page }) => {
  const name = unique("Edit Profile");
  await page.goto(`${list}/new`);
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("City", { exact: true }).fill("Umeå");
  await page.getByLabel("VAT number", { exact: true }).fill("SE1");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("created")).toBeVisible();

  await page.getByLabel("City", { exact: true }).fill("Luleå");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();

  expect(testRow(`select city || '|' || vat_number from customers where name = ${sql(name)}`)).toBe("Luleå|SE1");
});

test("a same-named customer of another organization keeps its own profile and cannot be reached", async ({ page, context }) => {
  // Both seeded organizations have an "Anna Andersson". Give mine a profile; theirs must not change.
  const mine = testRow(`select id from customers where organization_id = ${sql(ORG_A.id)} and name = 'Anna Andersson'`);
  const theirs = testRow(`select id from customers where organization_id = ${sql(ORG_B.id)} and name = 'Anna Andersson'`);
  const before = testRow(`select coalesce(city, '-') || '|' || coalesce(vat_number, '-') from customers where id = ${sql(theirs)}`);

  await page.goto(`${list}/${mine}`);
  const marker = `Isolation ${Date.now() % 100000}`;
  await page.getByLabel("City", { exact: true }).fill(marker);
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("saved")).toBeVisible();

  expect(testRow(`select coalesce(city, '-') || '|' || coalesce(vat_number, '-') from customers where id = ${sql(theirs)}`)).toBe(before);
  // Their record is a plain 404 through my organization, for read and for write.
  const read = await context.request.get(bffUrl(ORG_A.id, `/customers/${theirs}`));
  const write = await context.request.patch(bffUrl(ORG_A.id, `/customers/${theirs}`), { data: { city: "Hijacked" } });
  expect([read.status(), write.status()]).toEqual([404, 404]);
  expect(testRow(`select coalesce(city, '-') from customers where id = ${sql(theirs)}`)).not.toBe("Hijacked");
});
