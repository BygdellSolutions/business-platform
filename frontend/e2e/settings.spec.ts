import { expect, test } from "./fixtures";

import {
  FREDRIK,
  MARIA,
  ORG_A,
  ORG_B,
  bffUrl,
  createWorld,
  insertCurrencylessTransaction,
  insertCustomer,
  pick,
  signIn,
  sql,
  testRow,
  type RoleName,
  type World,
} from "./support";

/**
 * Organization settings and the currency foundation, in a real browser against the real stack and
 * the test database. Specs that CHANGE settings work in a throwaway organization of their own
 * (created straight in the test database), so the seeded organizations stay as other specs expect.
 */

let world: World;
test.afterEach(() => world?.cleanup());

const settings = (orgId: string) => `/o/${orgId}/settings`;
const orgRow = (orgId: string, columns: string) => testRow(`select ${columns} from organizations where id = ${sql(orgId)}`);

test.describe("owner and admin", () => {
  test("set the time zone; it persists, and new transactions start on the organization's date", async ({ page, context }) => {
    world = createWorld({ label: "TimeZone" });
    await signIn(context, world.email);

    await page.goto(settings(world.orgId));
    await expect(page.getByText(/Not set: new dates default to today in UTC/)).toBeVisible();
    await page.getByLabel("Time zone", { exact: true }).fill("Pacific/Kiritimati");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    expect(orgRow(world.orgId, "timezone")).toBe("Pacific/Kiritimati");

    await page.reload();
    await expect(page.getByLabel("Time zone", { exact: true })).toHaveValue("Pacific/Kiritimati");

    // The form's date is the organization's today as FastAPI computes it in that zone (UTC+14), not the browser's.
    const today = ((await (await context.request.get(bffUrl(world.orgId, "/organization"))).json()) as { today: string }).today;
    await page.goto(`/o/${world.orgId}/transactions/new`);
    await expect(page.getByLabel("Date")).toHaveValue(today);
  });

  test("an unknown time zone is refused next to the box and nothing is stored", async ({ page, context }) => {
    world = createWorld({ label: "BadZone" });
    await signIn(context, world.email);

    await page.goto(settings(world.orgId));
    await page.getByLabel("Time zone", { exact: true }).fill("Mars/Olympus");
    await page.getByTestId("submit").click();
    await expect(page.getByText(/must be an IANA time zone name/)).toBeVisible();
    expect(orgRow(world.orgId, "coalesce(timezone, '-')")).toBe("-");
  });

  test("edit the business profile; it persists in PostgreSQL and survives a reload", async ({ page, context }) => {
    world = createWorld({ label: "Profile" });
    await signIn(context, world.email);

    await page.goto(settings(world.orgId));
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await page.getByLabel("Legal name", { exact: true }).fill("Profile Hästterapi AB");
    await page.getByLabel("Address line 1", { exact: true }).fill("Storgatan 1");
    await page.getByLabel("Address line 2", { exact: true }).fill("c/o Anna");
    await page.getByLabel("Postal code", { exact: true }).fill("903 26");
    await page.getByLabel("City", { exact: true }).fill("Umeå");
    await page.getByLabel("Country code", { exact: true }).fill("se");
    await page.getByLabel("Registration number", { exact: true }).fill("556000-0001");
    await page.getByLabel("VAT number", { exact: true }).fill("SE556000000101");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("saved")).toBeVisible();
    await expect(page.getByLabel("Country code", { exact: true })).toHaveValue("SE"); // the stored, normalized value
    expect(orgRow(world.orgId, "legal_name || '|' || address_line1 || '|' || address_line2 || '|' || postal_code || '|' || city || '|' || country_code || '|' || registration_number || '|' || vat_number")).toBe(
      "Profile Hästterapi AB|Storgatan 1|c/o Anna|903 26|Umeå|SE|556000-0001|SE556000000101",
    );

    await page.reload();
    await expect(page.getByLabel("Legal name", { exact: true })).toHaveValue("Profile Hästterapi AB");
    await expect(page.getByLabel("City", { exact: true })).toHaveValue("Umeå");
    await expect(page.getByLabel("VAT number", { exact: true })).toHaveValue("SE556000000101");
  });

  test("the nav links the settings page", async ({ page, context }) => {
    world = createWorld({ label: "Nav" });
    await signIn(context, world.email);
    await page.goto(`/o/${world.orgId}`);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(settings(world.orgId));
  });

  test("a blank field is stored as no value", async ({ page, context }) => {
    world = createWorld({ label: "Blank" });
    await signIn(context, world.email);
    await page.goto(settings(world.orgId));
    await page.getByLabel("City", { exact: true }).fill("Umeå");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();

    await page.getByLabel("City", { exact: true }).fill("   ");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("saved")).toBeVisible();
    expect(orgRow(world.orgId, "city is null")).toBe("t");
  });

  test("a malformed country code is refused by the backend, shown at its box, and nothing is stored", async ({ page, context }) => {
    world = createWorld({ label: "BadCode" });
    await signIn(context, world.email);
    await page.goto(settings(world.orgId));
    await page.getByLabel("City", { exact: true }).fill("Umeå");
    await page.getByLabel("Country code", { exact: true }).fill("SWE");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-country_code")).toBeVisible();
    await expect(page.getByLabel("Country code", { exact: true })).toHaveValue("SWE"); // what was typed stays
    expect(orgRow(world.orgId, "(city is null) || '|' || (country_code is null)")).toBe("true|true");
  });

  test("an admin can edit as well", async ({ page, context }) => {
    world = createWorld({ label: "AdminEdit" });
    const admin = world.addMember("admin");
    await signIn(context, admin);
    await page.goto(settings(world.orgId));
    await page.getByLabel("City", { exact: true }).fill("Luleå");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    expect(orgRow(world.orgId, "city")).toBe("Luleå");
  });
});

test.describe("every other role", () => {
  for (const role of ["accountant", "employee", "viewer"] as RoleName[]) {
    test(`${role}: sees the settings read-only, and the server refuses a change attempted anyway`, async ({ page, context }) => {
      world = createWorld({ label: `ReadOnly-${role}` });
      testRow(`update organizations set city = 'Umeå', legal_name = 'Seen AB' where id = ${sql(world.orgId)}`);
      await signIn(context, world.addMember(role));

      await page.goto(settings(world.orgId));
      await expect(page.getByTestId("read-only")).toBeVisible();
      await expect(page.getByTestId("setting-city")).toHaveText("Umeå");
      await expect(page.getByTestId("setting-legal_name")).toHaveText("Seen AB");
      await expect(page.getByTestId("setting-default_currency")).toHaveText("SEK");
      await expect(page.getByTestId("submit")).toHaveCount(0);
      await expect(page.getByRole("textbox")).toHaveCount(0);

      // Hiding the form is a convenience; the server decides.
      const attempt = await context.request.patch(bffUrl(world.orgId, "/organization"), { data: { city: "Hacked" } });
      expect(attempt.status()).toBe(403);
      expect(orgRow(world.orgId, "city")).toBe("Umeå");
    });
  }

  test("a seeded employee (Maria) reads the settings of her organization but cannot change them", async ({ page, context }) => {
    await signIn(context, MARIA);
    await page.goto(settings(ORG_B.id));
    await expect(page.getByTestId("read-only")).toBeVisible();
    const attempt = await context.request.patch(bffUrl(ORG_B.id, "/organization"), { data: { city: "Hacked" } });
    expect(attempt.status()).toBe(403);
    expect(orgRow(ORG_B.id, "city")).toBe("Umeå"); // the seed's value, untouched
  });
});

test.describe("tenant isolation", () => {
  test("one organization's settings are invisible and unchangeable from another", async ({ page, context }) => {
    world = createWorld({ label: "Mine" });
    const other = createWorld({ label: "Theirs" });
    try {
      testRow(`update organizations set legal_name = 'Secret Theirs AB', vat_number = 'SE-THEIRS' where id = ${sql(other.orgId)}`);
      await signIn(context, world.email);

      // Asking for their organization's settings is the same 404 as for a random one.
      const read = await context.request.get(bffUrl(other.orgId, "/organization"));
      const randomRead = await context.request.get(bffUrl("00000000-0000-4000-8000-00000000dead", "/organization"));
      expect(read.status()).toBe(404);
      expect(randomRead.status()).toBe(404);
      expect(await read.text()).not.toContain("Secret");
      const write = await context.request.patch(bffUrl(other.orgId, "/organization"), { data: { legal_name: "Hijacked" } });
      expect(write.status()).toBe(404);

      // The page shows 404 for their id and my own settings never contain their values.
      const response = await page.goto(settings(other.orgId));
      expect(response?.status()).toBe(404);
      await page.goto(settings(world.orgId));
      await expect(page.getByLabel("Legal name", { exact: true })).toHaveValue("");
      await expect(page.getByLabel("VAT number", { exact: true })).toHaveValue("");
      expect(orgRow(other.orgId, "legal_name || '|' || vat_number")).toBe("Secret Theirs AB|SE-THEIRS");

      // Saving mine never touches theirs.
      await page.getByLabel("Legal name", { exact: true }).fill("Mine AB");
      await page.getByTestId("submit").click();
      await expect(page.getByTestId("saved")).toBeVisible();
      expect(orgRow(other.orgId, "legal_name")).toBe("Secret Theirs AB");
    } finally {
      other.cleanup();
    }
  });

  test("Fredrik, owner of one organization and admin of another, sees the profile of the one in the address", async ({ page, context }) => {
    // Read-only checks on the seeded organizations: nothing is saved.
    await signIn(context, FREDRIK);
    await page.goto(settings(ORG_A.id));
    await expect(page.getByLabel("Legal name", { exact: true })).toHaveValue("Fredrik Horse Therapy AB");
    await page.goto(settings(ORG_B.id));
    await expect(page.getByLabel("Legal name", { exact: true })).toHaveValue("Umeå Stable Services AB");
  });
});

test.describe("the default currency", () => {
  test("a seeded organization has SEK, explicitly, and cannot change it because prices exist", async ({ page, context }) => {
    await signIn(context, FREDRIK);
    await page.goto(settings(ORG_A.id));

    await expect(page.getByLabel("Default currency", { exact: true })).toHaveValue("SEK");
    await expect(page.getByLabel("Default currency", { exact: true })).toBeDisabled();
    await expect(page.getByText(/can no longer be changed because prices already exist/)).toBeVisible();

    // The server refuses it too, and nothing changes.
    const attempt = await context.request.patch(bffUrl(ORG_A.id, "/organization"), { data: { default_currency: "EUR" } });
    expect(attempt.status()).toBe(409);
    expect((await attempt.json()).detail.code).toBe("currency_locked");
    expect(orgRow(ORG_A.id, "default_currency")).toBe("SEK");
  });

  test("an organization with no currency cannot create transactions until one is set; once prices exist the currency is fixed", async ({ page, context }) => {
    world = createWorld({ currency: null, label: "NoCurrency" });
    await signIn(context, world.email);
    const customerId = insertCustomer(world.orgId, "Billing Co");

    // Not configured: the API refuses a transaction, and says why.
    const refused = await context.request.post(bffUrl(world.orgId, "/transactions"), { data: { billing_customer_id: customerId } });
    expect(refused.status()).toBe(409);
    expect((await refused.json()).detail.code).toBe("currency_not_configured");
    expect(testRow(`select count(*) from transactions where organization_id = ${sql(world.orgId)}`)).toBe("0");

    // The create form shows the same reason.
    await page.goto(`/o/${world.orgId}/transactions/new`);
    await pick(page, "billing_customer_id", "Billing Co");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("form-error")).toContainText("no default currency");

    // Set the currency in the settings (typed in lower case; stored upper case).
    await page.goto(settings(world.orgId));
    await expect(page.getByText(/Transactions cannot be created until a currency is set/)).toBeVisible();
    await page.getByLabel("Default currency", { exact: true }).fill("eur");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    await expect(page.getByLabel("Default currency", { exact: true })).toHaveValue("EUR");
    expect(orgRow(world.orgId, "default_currency")).toBe("EUR");

    // Nothing has a price yet, so it is still changeable ...
    await expect(page.getByLabel("Default currency", { exact: true })).toBeEnabled();
    await page.getByLabel("Default currency", { exact: true }).fill("NOK");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    expect(orgRow(world.orgId, "default_currency")).toBe("NOK");

    // ... until the first transaction exists: it snapshots NOK, and then the currency is fixed.
    const made = await context.request.post(bffUrl(world.orgId, "/transactions"), { data: { billing_customer_id: customerId } });
    expect(made.status()).toBe(201);
    expect((await made.json()).currency).toBe("NOK");
    await page.reload();
    await expect(page.getByLabel("Default currency", { exact: true })).toBeDisabled();
    const attempt = await context.request.patch(bffUrl(world.orgId, "/organization"), { data: { default_currency: "SEK" } });
    expect(attempt.status()).toBe(409);
    expect(orgRow(world.orgId, "default_currency")).toBe("NOK");
  });

  test("a new transaction shows the organization's currency, in its page and in the list", async ({ page, context }) => {
    world = createWorld({ currency: "DKK", label: "ShowsCurrency" });
    await signIn(context, world.email);
    const customerId = insertCustomer(world.orgId, "Billing Co");
    const made = await context.request.post(bffUrl(world.orgId, "/transactions"), { data: { billing_customer_id: customerId } });
    const id = (await made.json()).id as string;

    await page.goto(`/o/${world.orgId}/transactions/${id}`);
    await expect(page.getByTestId("total-currency")).toHaveText("DKK");
    await page.goto(`/o/${world.orgId}/transactions`);
    await expect(page.getByTestId("transaction-currency")).toHaveText("DKK");
    expect(testRow(`select currency from transactions where id = ${sql(id)}`)).toBe("DKK");
  });
});

test.describe("transactions that predate currencies", () => {
  test("stay without a currency until an owner explicitly assigns one; nothing is silently labelled", async ({ page, context }) => {
    world = createWorld({ currency: null, label: "Legacy" });
    await signIn(context, world.email);
    const customerId = insertCustomer(world.orgId, "Old Customer");
    const first = insertCurrencylessTransaction(world.orgId, customerId, "2026-01-15");
    const second = insertCurrencylessTransaction(world.orgId, customerId, "2026-01-16");

    // They are shown as having no currency (not as SEK, not blank).
    await page.goto(`/o/${world.orgId}/transactions/${first}`);
    await expect(page.getByTestId("total-currency")).toHaveText("No currency recorded");
    await page.goto(`/o/${world.orgId}/transactions`);
    await expect(page.getByTestId("transaction-currency")).toHaveText(["none", "none"]);

    // The settings explain it and ask for the organization's currency first.
    await page.goto(settings(world.orgId));
    await expect(page.getByTestId("earlier-count")).toContainText("2 transactions were created before currencies existed");
    await expect(page.getByTestId("assign-currency")).toHaveCount(0);

    // Setting the organization's currency does NOT touch them either.
    await page.getByLabel("Default currency", { exact: true }).fill("SEK");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    expect(testRow(`select count(*) from transactions where organization_id = ${sql(world.orgId)} and currency is not null`)).toBe("0");

    // Only the explicit, confirmed action assigns it.
    await expect(page.getByTestId("assign-currency")).toBeVisible();
    await page.getByTestId("assign-currency").click();
    await expect(page.getByText(/Assign SEK to 2 transactions\? This cannot be undone\./)).toBeVisible();
    await page.getByTestId("assign-currency-keep").click();
    expect(testRow(`select count(*) from transactions where organization_id = ${sql(world.orgId)} and currency is not null`)).toBe("0"); // declining changes nothing

    await page.getByTestId("assign-currency").click();
    await page.getByTestId("assign-currency-confirm").click();
    await expect(page.getByTestId("assigned")).toHaveText(/2 transactions now have the currency SEK/);

    expect(testRow(`select string_agg(currency, ',') from transactions where organization_id = ${sql(world.orgId)}`)).toBe("SEK,SEK");
    await page.goto(`/o/${world.orgId}/transactions/${second}`);
    await expect(page.getByTestId("total-currency")).toHaveText("SEK");
    await page.goto(settings(world.orgId));
    await expect(page.getByTestId("earlier-transactions")).toHaveCount(0); // nothing left to assign
  });

  test("an employee sees them but cannot assign a currency, and the server refuses it", async ({ page, context }) => {
    world = createWorld({ currency: "SEK", label: "LegacyEmployee" });
    const customerId = insertCustomer(world.orgId, "Old Customer");
    const old = insertCurrencylessTransaction(world.orgId, customerId);
    await signIn(context, world.addMember("employee"));

    await page.goto(settings(world.orgId));
    await expect(page.getByTestId("earlier-count")).toContainText("1 transaction was created");
    await expect(page.getByTestId("assign-currency")).toHaveCount(0);
    const attempt = await context.request.post(bffUrl(world.orgId, "/transactions/assign-currency"), { data: { currency: "SEK" } });
    expect(attempt.status()).toBe(403);
    expect(testRow(`select currency is null from transactions where id = ${sql(old)}`)).toBe("t");
  });

  test("assigning in one organization never touches another's currency-less transactions", async ({ context }) => {
    world = createWorld({ currency: "SEK", label: "AssignMine" });
    const other = createWorld({ currency: "SEK", label: "AssignTheirs" });
    try {
      const mine = insertCurrencylessTransaction(world.orgId, insertCustomer(world.orgId, "C1"));
      const theirs = insertCurrencylessTransaction(other.orgId, insertCustomer(other.orgId, "C2"));
      await signIn(context, world.email);

      const done = await context.request.post(bffUrl(world.orgId, "/transactions/assign-currency"), { data: { currency: "SEK" } });
      expect(await done.json()).toEqual({ currency: "SEK", assigned: 1 });

      expect(testRow(`select currency from transactions where id = ${sql(mine)}`)).toBe("SEK");
      expect(testRow(`select currency is null from transactions where id = ${sql(theirs)}`)).toBe("t");
    } finally {
      other.cleanup();
    }
  });
});
