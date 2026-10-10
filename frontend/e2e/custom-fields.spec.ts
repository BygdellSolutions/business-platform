import { expect, test, waitForHydration, type Page } from "./fixtures";

import { BACKEND_URL, BASE_URL } from "./env";
import {
  FREDRIK,
  ORG_A,
  addLine,
  bffUrl,
  createCustomer,
  createHorse,
  createItem,
  createTransaction,
  getTransaction,
  idOf,
  lifecycle,
  openAddLine,
  pick,
  picker,
  setActive,
  signIn,
  signInViaUi,
  sql,
  storedValues,
  testRow,
  unique,
  uniqueKey,
  withDefinitions,
  withRequired,
} from "./support";

/**
 * Custom fields on transactions, in a real browser against the real stack and the dedicated
 * test database. The headline is the workflow the product is for:
 *
 *     Umeå HK (billing) -> Anna Andersson (Owner) -> Kalle (Horse) -> Horse massage
 *
 * where Owner and Horse are GENERIC reference fields (seeded as data, not code) and Horse's
 * choices depend on the Owner. Every test that makes a field required or creates a synthetic
 * field puts everything back, whatever happens.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const txUrl = (id: string) => `/o/${ORG_A.id}/transactions/${id}`;
const rows = (page: Page) => page.getByTestId("line-row");
const linePanel = (page: Page) => page.locator('[data-testid="custom-fields"][data-entity-type="transaction_line"]').first();
const txPanel = (page: Page) => page.locator('[data-testid="custom-fields"][data-entity-type="transaction"]').first();
const ownerBox = (page: Page) => picker(page, "owner").getByRole("combobox");
const horseBox = (page: Page) => picker(page, "horse").getByRole("combobox");

/** A transaction in organization A with one ad-hoc line, billed to a fresh customer. */
async function draftWithLine(context: Parameters<typeof createCustomer>[0], description = "Fieldwork") {
  const customer = await createCustomer(context, ORG_A.id, unique("Field Billing"));
  const transaction = await createTransaction(context, ORG_A.id, { billing_customer_id: customer.id, transaction_date: "2026-10-01" });
  const line = await addLine(context, ORG_A.id, transaction.id, { description, unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" });
  return { transaction, line, url: txUrl(transaction.id) };
}

/** An owner (a customer) with one horse, in organization A. */
async function ownerWithHorse(context: Parameters<typeof createCustomer>[0], label: string) {
  const owner = await createCustomer(context, ORG_A.id, unique(`${label} Owner`));
  const horse = await createHorse(context, ORG_A.id, { name: unique(`${label} Horse`), owner_customer_id: owner.id });
  return { owner, horse };
}

async function setValues(context: Parameters<typeof createCustomer>[0], entityType: string, id: string, values: Record<string, unknown>) {
  const response = await context.request.patch(bffUrl(ORG_A.id, `/custom-fields/entities/${entityType}/${id}/values`), { data: { values } });
  expect(response.status(), await response.text()).toBe(200);
}

test.describe("the real workflow", () => {
  test("Umeå HK -> Anna Andersson -> Kalle -> Horse massage, from sign-in to a completed, read-only transaction", async ({ page, context }) => {
    const anna = idOf("customers", ORG_A.id, "Anna Andersson");
    const kalle = idOf("horses", ORG_A.id, "Kalle");
    const { owner: erik, horse: storm } = await ownerWithHorse(context, "Other");
    const requests: string[] = [];
    page.on("request", (request) => requests.push(`${request.method()} ${request.url()}`));

    await withRequired(context, ORG_A.id, "transaction_line", ["owner", "horse"], async () => {
      // 1-2. Sign in and choose the organization.
      await signInViaUi(page, FREDRIK);
      await page.getByRole("link", { name: ORG_A.name }).click();
      await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);

      // 3. A transaction billed to Umeå HK.
      await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Orders" }).click();
      await page.getByTestId("new-transaction").click();
      // A client-side navigation does not wait for the new page: the transactions LIST has a picker with the same test id (its
      // filter), so interacting before the form has replaced the list acts on the wrong, soon unmounted, picker.
      await expect(page).toHaveURL(/\/transactions\/new$/);
      await pick(page, "billing_customer_id", "Umeå HK");
      await page.getByTestId("submit").click();
      await expect(page.getByTestId("created")).toBeVisible();
      await expect(page.getByTestId("header-customer")).toContainText("Umeå HK");
      const txId = page.url().match(/transactions\/([0-9a-f-]{36})/)![1];

      // 4. Add Horse massage (a catalog item).
      await openAddLine(page);
      await pick(page, "item_id", "Horse massage");
      await page.getByLabel("Quantity", { exact: true }).fill("1");
      await page.getByTestId("submit-line").click();
      await expect(rows(page)).toHaveCount(1);
      await expect(rows(page).first().getByTestId("line-description")).toHaveText("Horse massage");
      const lineId = (await getTransaction(context, ORG_A.id, txId)).lines[0].id;

      // The fields are marked required, and nothing is set yet.
      await expect(linePanel(page).getByTestId("cf-owner")).toContainText("Not set");
      await expect(linePanel(page).getByTestId("cf-owner").locator("dt")).toHaveAttribute("data-required", "true");

      // 5. Completion is attempted with the required fields empty: the BACKEND blocks it, and each
      // problem is shown at the right line and the right field.
      await page.getByTestId("invoice-order").click();
      await expect(page.getByTestId("editor-notice")).toContainText("blocked");
      await expect(page.getByTestId("tx-status")).toHaveText("Draft");
      await expect(page.getByTestId("editor-problems")).toContainText("Line 1 · Owner: Owner is required");
      await expect(page.getByTestId("editor-problems")).toContainText("Line 1 · Horse: Horse is required");
      await expect(linePanel(page).getByTestId("cf-owner").getByTestId("error-owner")).toHaveText("Owner is required");
      await expect(linePanel(page).getByTestId("cf-horse").getByTestId("error-horse")).toHaveText("Horse is required");
      await expect(page.getByTestId("editor-problems").getByRole("link").first()).toHaveAttribute("href", `#line-${lineId}-fields`);
      expect(testRow(`select status from transactions where id = ${sql(txId)}`)).toBe("draft");

      // 6. Edit the fields: Horse waits for an Owner.
      const versionBefore = (await getTransaction(context, ORG_A.id, txId)).version;
      await page.getByTestId("edit-fields").click();
      const fromHere = requests.length; // everything the page asks from now on belongs to the custom fields form
      await expect(horseBox(page)).toBeDisabled();
      await expect(page.getByText("Choose Owner first.")).toBeVisible();
      await expect(page.getByTestId("invoice-order")).toBeDisabled(); // an open editor

      // Select Anna.
      await pick(page, "owner", "Anna Andersson");
      await expect(horseBox(page)).toBeEnabled();

      // 7. Horse choices are filtered by the owner: by the BACKEND, with Anna's UUID.
      await horseBox(page).click();
      await expect(picker(page, "horse").getByRole("option")).not.toHaveCount(0);
      const horseOptions = (await picker(page, "horse").getByRole("option").allTextContents()).join("|");
      expect(horseOptions).toContain("Kalle");
      expect(horseOptions).not.toContain(storm.name);
      const asked = requests.slice(fromHere);
      expect(asked.some((request) => request.includes("/custom-fields/definitions/") && request.includes("/choices") && request.includes(`depends_on_value=${anna}`))).toBe(true);
      // The Custom Fields layer asked only the generic choices endpoint, never the customers or horses of any module.
      expect(asked.filter((request) => /\/api\/o\/[^/]+\/(customers|horses|items)\b/.test(request))).toEqual([]);

      // 8. Select Kalle, and save both in one request, as UUIDs.
      await picker(page, "horse").getByRole("option").filter({ hasText: "Kalle" }).first().click();
      const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/custom-fields/entities/transaction_line/"));
      await page.getByTestId("save-fields").click();
      expect(JSON.parse((await request).postData() ?? "")).toEqual({ values: { owner: anna, horse: kalle } });
      await expect(linePanel(page).getByTestId("cf-owner")).toContainText("Anna Andersson");
      await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Kalle");
      await expect(linePanel(page).getByTestId("error-owner")).toHaveCount(0); // the required-field errors went away
      await expect(page.getByTestId("editor-problems")).toHaveCount(0);
      expect(storedValues(lineId)).toBe(`horse=${kalle},owner=${anna}`);
      expect((await getTransaction(context, ORG_A.id, txId)).version).toBe(versionBefore); // custom fields do not touch Sales versions

      // 9. Complete.
      await page.getByTestId("invoice-order").click();
      await expect(page.getByTestId("tx-status")).toHaveText("Completed");
      await expect(page.getByTestId("editor-notice")).toHaveCount(0);

      // 10. Read-only, and still showing Anna and Kalle (also after a reload).
      for (const reload of [false, true]) {
        if (reload) await page.reload();
        await expect(page.getByTestId("edit-fields")).toHaveCount(0);
        await expect(page.getByRole("combobox")).toHaveCount(0);
        await expect(linePanel(page).getByTestId("cf-owner")).toContainText("Anna Andersson");
        await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Kalle");
      }
      expect(testRow(`select status from transactions where id = ${sql(txId)}`)).toBe("completed");
    });
    expect(erik.id).toBeTruthy();
  });
});

test.describe("dependencies", () => {
  test("changing the Owner after a Horse was chosen clears the Horse, and one request carries both", async ({ page, context }) => {
    const { owner, horse } = await ownerWithHorse(context, "Change");
    const { owner: other, horse: otherHorse } = await ownerWithHorse(context, "Replacement");
    const { line, url } = await draftWithLine(context);
    await setValues(context, "transaction_line", line.id, { owner: owner.id, horse: horse.id });

    await page.goto(url);
    await expect(linePanel(page).getByTestId("cf-horse")).toContainText(horse.name);
    await page.getByTestId("edit-fields").click();
    await expect(horseBox(page)).toHaveValue(horse.name);
    await pick(page, "owner", other.name);

    await expect(horseBox(page)).toHaveValue(""); // cleared at once
    await horseBox(page).click();
    await expect(picker(page, "horse").getByRole("option").filter({ hasText: otherHorse.name })).toHaveCount(1); // the new owner's horses
    await expect(picker(page, "horse").getByRole("option").filter({ hasText: horse.name })).toHaveCount(0);
    await page.keyboard.press("Escape");

    const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/values"));
    await page.getByTestId("save-fields").click();
    expect(JSON.parse((await request).postData() ?? "")).toEqual({ values: { owner: other.id, horse: null } });
    await expect(linePanel(page).getByTestId("cf-owner")).toContainText(other.name);
    await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Not set");
    expect(storedValues(line.id)).toBe(`owner=${other.id}`); // the old horse is gone from the server too
  });

  test("clearing the Owner clears the Horse", async ({ page, context }) => {
    const { owner, horse } = await ownerWithHorse(context, "Clear");
    const { line, url } = await draftWithLine(context);
    await setValues(context, "transaction_line", line.id, { owner: owner.id, horse: horse.id });
    await page.goto(url);

    await page.getByTestId("edit-fields").click();
    await picker(page, "owner").getByRole("button", { name: "Clear Owner" }).click();
    await expect(horseBox(page)).toHaveValue("");
    await expect(horseBox(page)).toBeDisabled();

    const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/values"));
    await page.getByTestId("save-fields").click();
    expect(JSON.parse((await request).postData() ?? "")).toEqual({ values: { owner: null, horse: null } });
    expect(storedValues(line.id)).toBe("");
  });

  test("with Horse required, saving only a new Owner is refused by the backend on the Horse control and nothing is half-saved", async ({ page, context }) => {
    const { owner, horse } = await ownerWithHorse(context, "Half");
    const { owner: other } = await ownerWithHorse(context, "Half Other");
    const { line, url } = await draftWithLine(context);
    await setValues(context, "transaction_line", line.id, { owner: owner.id, horse: horse.id });

    await withRequired(context, ORG_A.id, "transaction_line", ["owner", "horse"], async () => {
      await page.goto(url);
      await page.getByTestId("edit-fields").click();
      await pick(page, "owner", other.name); // clears the Horse, which is required
      await page.getByTestId("save-fields").click();

      await expect(page.getByTestId("error-horse")).toHaveText("Horse is required");
      await expect(page.getByTestId("error-owner")).toHaveCount(0);
      await expect(ownerBox(page)).toHaveValue(other.name); // the draft is kept
      expect(storedValues(line.id)).toBe(`horse=${horse.id},owner=${owner.id}`); // the server still has the old, consistent pair
    });
  });

});

test.describe("the six value types", () => {
  test("text, number, date, boolean, select and reference are saved in their own types and shown as stored", async ({ page, context }) => {
    const note = uniqueKey("note");
    const qty = uniqueKey("qty");
    const due = uniqueKey("due");
    const flag = uniqueKey("flag");
    const size = uniqueKey("size");
    const client = uniqueKey("client");
    const { transaction, url } = await draftWithLine(context);
    const someone = await createCustomer(context, ORG_A.id, unique("Reference Target"));

    await withDefinitions(
      context,
      ORG_A.id,
      [
        { entity_type: "transaction", key: note, label: "Note T", field_type: "text", position: 1 },
        { entity_type: "transaction", key: qty, label: "Qty T", field_type: "number", position: 2 },
        { entity_type: "transaction", key: due, label: "Due T", field_type: "date", position: 3 },
        { entity_type: "transaction", key: flag, label: "Flag T", field_type: "boolean", position: 4 },
        { entity_type: "transaction", key: size, label: "Size T", field_type: "select", position: 5, options: [{ label: "Small" }, { label: "Large" }] },
        { entity_type: "transaction", key: client, label: "Client T", field_type: "reference", position: 6, reference: { source: "customer" } },
        { entity_type: "transaction", key: uniqueKey("hidden"), label: "Hidden T", field_type: "text", position: 7, show_in_form: false },
      ],
      async (created) => {
        const options = (created[4] as unknown as { options: { id: string; label: string }[] }).options;
        const large = options.find((option) => option.label === "Large")!.id;

        await page.goto(url);
        const panel = txPanel(page);
        await expect(panel.getByTestId(`cf-${note}`)).toContainText("Not set");
        await expect(panel.getByText("Hidden T")).toHaveCount(0); // not meant for forms

        await panel.getByTestId("edit-fields").click();
        await panel.getByLabel("Note T").fill("Spring campaign");
        await panel.getByLabel("Qty T").fill("0.10");
        await panel.getByLabel("Due T").fill("2026-12-24");
        await panel.getByLabel("Flag T").selectOption("No");
        await panel.getByLabel("Size T").selectOption("Large");
        await picker(page, client).getByRole("combobox").fill(someone.name);
        await picker(page, client).getByRole("option").first().click();

        const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/entities/transaction/"));
        await panel.getByTestId("save-fields").click();
        const body = JSON.parse((await request).postData() ?? "") as { values: Record<string, unknown> };

        // What went over the wire: each in its own type, UUIDs for select and reference, a STRING for the number.
        expect(body.values).toEqual({ [note]: "Spring campaign", [qty]: "0.10", [due]: "2026-12-24", [flag]: false, [size]: large, [client]: someone.id });
        expect(typeof body.values[qty]).toBe("string");
        expect(body.values[flag]).toBe(false);

        // What is stored, and what is shown.
        await expect(panel.getByTestId(`cf-${note}`)).toContainText("Spring campaign");
        await expect(panel.getByTestId(`cf-${flag}`)).toContainText("No"); // false, not "Not set"
        await expect(panel.getByTestId(`cf-${size}`)).toContainText("Large");
        await expect(panel.getByTestId(`cf-${client}`)).toContainText(someone.name);
        expect(storedValues(transaction.id).split(",").sort()).toEqual(
          [`${note}=Spring campaign`, `${qty}=0.1000`, `${due}=2026-12-24`, `${flag}=false`, `${size}=${large}`, `${client}=${someone.id}`].sort(),
        );
        // The number is shown exactly as FastAPI returns it (asked directly), not reformatted by the page.
        const direct = await context.request.get(`${BACKEND_URL}/api/custom-fields/entities/transaction/${transaction.id}/values`, { headers: { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id } });
        const shownByApi = ((await direct.json()) as { values: { key: string; value: unknown }[] }).values.find((value) => value.key === qty)!.value;
        expect(typeof shownByApi).toBe("string");
        await expect(panel.getByTestId(`cf-${qty}`)).toContainText(shownByApi as string);
      },
    );
  });

  for (const typed of ["4.35", "8.20", "9999999999.99", "-12.5", "100"]) {
    test(`a custom number typed as ${typed} travels as that string, is stored exactly, and is shown as the backend returns it`, async ({ page, context }) => {
      const key = uniqueKey("num");
      const { transaction, url } = await draftWithLine(context);
      await withDefinitions(context, ORG_A.id, [{ entity_type: "transaction", key, label: "Num T", field_type: "number" }], async () => {
        await page.goto(url);
        await txPanel(page).getByTestId("edit-fields").click();
        await txPanel(page).getByLabel("Num T").fill(typed);
        const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/entities/transaction/"));
        await txPanel(page).getByTestId("save-fields").click();
        expect((await request).postData()).toBe(JSON.stringify({ values: { [key]: typed } })); // a JSON STRING

        const stored = testRow(`select value_number::text from custom_field_values v join custom_field_definitions d on d.id = v.definition_id where v.entity_id = ${sql(transaction.id)} and d.key = ${sql(key)}`);
        const expected = typed.includes(".") ? typed.replace(/0+$/, "").replace(/\.$/, "") : typed; // the backend drops trailing zeros
        await expect(txPanel(page).getByTestId(`cf-${key}`)).toContainText(expected);
        expect(Number.isNaN(Number(stored))).toBe(false);
        expect(stored.replace(/0+$/, "").replace(/\.$/, "")).toBe(expected);
      });
    });
  }

  test("a boolean keeps three states: not set, yes and no", async ({ page, context }) => {
    const key = uniqueKey("flag");
    const { transaction, url } = await draftWithLine(context);
    await withDefinitions(context, ORG_A.id, [{ entity_type: "transaction", key, label: "Flag T", field_type: "boolean" }], async () => {
      const state = () => testRow(`select coalesce((select v.value_boolean::text from custom_field_values v join custom_field_definitions d on d.id = v.definition_id where v.entity_id = ${sql(transaction.id)} and d.key = ${sql(key)}), 'no row')`);
      await page.goto(url);
      await expect(txPanel(page).getByTestId(`cf-${key}`)).toContainText("Not set");
      expect(state()).toBe("no row");

      const choose = async (label: "Yes" | "No" | "Not set") => {
        await txPanel(page).getByTestId("edit-fields").click();
        await txPanel(page).getByLabel("Flag T").selectOption(label);
        const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/entities/transaction/"));
        await txPanel(page).getByTestId("save-fields").click();
        return JSON.parse((await request).postData() ?? "").values[key];
      };

      expect(await choose("No")).toBe(false); // unset -> No: false is sent, not null
      await expect(txPanel(page).getByTestId(`cf-${key}`)).toContainText("No");
      expect(state()).toBe("false");
      expect(await choose("Yes")).toBe(true);
      expect(state()).toBe("true");
      expect(await choose("Not set")).toBeNull();
      await expect(txPanel(page).getByTestId(`cf-${key}`)).toContainText("Not set");
      expect(state()).toBe("no row");
    });
  });

  test("a required transaction-level field blocks completion at its control, and completion works once it is supplied", async ({ page, context }) => {
    const key = uniqueKey("proj");
    const { transaction, url } = await draftWithLine(context);
    await withDefinitions(context, ORG_A.id, [{ entity_type: "transaction", key, label: "Project T", field_type: "text", required: true }], async () => {
      await page.goto(url);
      await expect(txPanel(page).getByTestId(`cf-${key}`).locator("dt")).toHaveAttribute("data-required", "true");

      await page.getByTestId("invoice-order").click();
      await expect(txPanel(page).getByTestId(`cf-${key}`).getByTestId(`error-${key}`)).toHaveText("Project T is required");
      await expect(page.getByTestId("editor-problems")).toContainText("Order · Project T: Project T is required");
      await expect(page.getByTestId("editor-problems").getByRole("link").first()).toHaveAttribute("href", "#transaction-fields");

      await txPanel(page).getByTestId("edit-fields").click();
      await txPanel(page).getByLabel("Project T").fill("Supplied");
      await txPanel(page).getByTestId("save-fields").click();
      await expect(txPanel(page).getByTestId(`error-${key}`)).toHaveCount(0);

      await page.getByTestId("invoice-order").click();
      await expect(page.getByTestId("tx-status")).toHaveText("Completed");
      await expect(txPanel(page).getByTestId(`cf-${key}`)).toContainText("Supplied");
      await expect(page.getByTestId("edit-fields")).toHaveCount(0);
      expect(testRow(`select status from transactions where id = ${sql(transaction.id)}`)).toBe("completed");
    });
  });

  test("a value the backend refuses (a text that is too long) shows on that control and keeps the draft", async ({ page, context }) => {
    const key = uniqueKey("long");
    const { url } = await draftWithLine(context);
    await withDefinitions(context, ORG_A.id, [{ entity_type: "transaction", key, label: "Long T", field_type: "text" }], async () => {
      await page.goto(url);
      await txPanel(page).getByTestId("edit-fields").click();
      await txPanel(page).getByLabel("Long T").fill("x".repeat(5000));
      await txPanel(page).getByTestId("save-fields").click();
      await expect(txPanel(page).getByTestId(`error-${key}`)).toContainText("characters");
      await expect(txPanel(page).getByLabel("Long T")).toHaveValue("x".repeat(5000));
    });
  });

  test("a disabled definition is not shown", async ({ page, context }) => {
    const key = uniqueKey("gone");
    const { url } = await draftWithLine(context);
    await withDefinitions(context, ORG_A.id, [{ entity_type: "transaction", key, label: "Gone T", field_type: "text" }], async (created) => {
      const off = await context.request.patch(bffUrl(ORG_A.id, `/custom-fields/definitions/${created[0].id}`), { data: { enabled: false } });
      expect(off.status()).toBe(200);
      await page.goto(url);
      await expect(page.getByText("Gone T")).toHaveCount(0);
    });
  });
});

test.describe("inactive and missing references", () => {
  test("an inactive owner and horse are still shown (in the list, in the form, and once completed), and are not newly assignable", async ({ page, context }) => {
    const { owner, horse } = await ownerWithHorse(context, "Retired");
    const { owner: active, horse: activeHorse } = await ownerWithHorse(context, "Active");
    const noteKey = uniqueKey("note");
    const { transaction, line, url } = await draftWithLine(context);
    await setValues(context, "transaction_line", line.id, { owner: owner.id, horse: horse.id });
    await setActive(context, ORG_A.id, "horses", horse.id, false);
    await setActive(context, ORG_A.id, "customers", owner.id, false);

    await withDefinitions(context, ORG_A.id, [{ entity_type: "transaction_line", key: noteKey, label: "Note L", field_type: "text" }], async () => {
      await page.goto(url);
      await expect(linePanel(page).getByTestId("cf-owner")).toContainText(`${owner.name}`);
      await expect(linePanel(page).getByTestId("cf-owner")).toContainText("(inactive)");
      await expect(linePanel(page).getByTestId("cf-horse")).toContainText(horse.name);
      await expect(linePanel(page).getByTestId("cf-horse")).toContainText("(inactive)");

      await page.getByTestId("edit-fields").click();
      await expect(ownerBox(page)).toHaveValue(`${owner.name} (inactive)`);
      await expect(horseBox(page)).toHaveValue(`${horse.name} (inactive)`);

      // Editing something else does not resend, clear or refuse the inactive pair.
      await linePanel(page).getByLabel("Note L").fill("only this");
      const request = page.waitForRequest((r) => r.method() === "PATCH" && r.url().includes("/values"));
      await page.getByTestId("save-fields").click();
      expect(JSON.parse((await request).postData() ?? "")).toEqual({ values: { [noteKey]: "only this" } });
      await expect(linePanel(page).getByTestId("cf-owner")).toContainText("(inactive)");
      expect(storedValues(line.id)).toContain(`owner=${owner.id}`);
      expect(storedValues(line.id)).toContain(`horse=${horse.id}`);

      // Not newly assignable: neither the inactive owner nor an inactive horse of an active owner is offered.
      await page.getByTestId("edit-fields").click();
      await ownerBox(page).fill(owner.name);
      await expect(picker(page, "owner").getByText("No matches")).toBeVisible();
      await ownerBox(page).fill("");
      await ownerBox(page).fill(active.name);
      await picker(page, "owner").getByRole("option").first().click();
      await horseBox(page).click();
      await expect(picker(page, "horse").getByRole("option").filter({ hasText: activeHorse.name })).toHaveCount(1);
      await expect(picker(page, "horse").getByRole("option").filter({ hasText: horse.name })).toHaveCount(0);
    });

    // And a completed transaction shows them just the same.
    await lifecycle(context, ORG_A.id, transaction.id, "complete");
    await page.goto(url);
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");
    await expect(linePanel(page).getByTestId("cf-horse")).toContainText(horse.name);
    await expect(linePanel(page).getByTestId("cf-owner")).toContainText("(inactive)");
  });

  test("a reference whose target no longer exists is shown safely instead of crashing the page, and is not replaced", async ({ page, context }) => {
    // Referenced records cannot be deleted through the API (that is guarded), so the dangling value is made by hand in the TEST database.
    const target = await createCustomer(context, ORG_A.id, unique("Soon Gone"));
    const { line, url } = await draftWithLine(context);
    await setValues(context, "transaction_line", line.id, { owner: target.id });
    testRow(`delete from customers where id = ${sql(target.id)} returning id`);

    await page.goto(url);
    await expect(page.getByTestId("tx-status")).toHaveText("Draft"); // the page rendered
    await expect(linePanel(page).getByTestId("cf-owner")).toContainText("(no longer exists)");
    await page.getByTestId("edit-fields").click();
    await expect(ownerBox(page)).toHaveValue("(no longer exists)");
    expect(storedValues(line.id)).toBe(`owner=${target.id}`); // nothing was substituted for it
  });
});

test.describe("completing and writing at the same time", () => {
  test("a field write that arrives after another tab completed is refused (locked), saves nothing, and explains itself", async ({ page, context }) => {
    const { owner, horse } = await ownerWithHorse(context, "Locked");
    const { transaction, line, url } = await draftWithLine(context);
    await page.goto(url);
    await page.getByTestId("edit-fields").click();
    await pick(page, "owner", owner.name);
    await pick(page, "horse", horse.name);
    await lifecycle(context, ORG_A.id, transaction.id, "complete"); // another tab completes first

    await page.getByTestId("save-fields").click();

    await expect(page.getByTestId("editor-notice")).toContainText("no longer a draft, so your changes could not be saved");
    await expect(page.getByTestId("tx-status")).toHaveText("Completed");
    await expect(page.getByTestId("custom-fields-form")).toHaveCount(0);
    expect(storedValues(line.id)).toBe("");
  });

  test("completion and a field write at the same instant: never completed without the required values", async ({ browser, context }) => {
    await withRequired(context, ORG_A.id, "transaction_line", ["owner", "horse"], async () => {
      for (let round = 0; round < 4; round += 1) {
        const { owner, horse } = await ownerWithHorse(context, `Race${round}`);
        const { transaction, line, url } = await draftWithLine(context);
        const writerContext = await browser.newContext({ baseURL: BASE_URL });
        const completerContext = await browser.newContext({ baseURL: BASE_URL });
        try {
          const [writer, completer] = [waitForHydration(await writerContext.newPage()), waitForHydration(await completerContext.newPage())];
          for (const [tab, tabContext] of [[writer, writerContext], [completer, completerContext]] as const) {
            await signIn(tabContext, FREDRIK);
            await tab.goto(url);
          }
          await writer.getByTestId("edit-fields").click();
          await pick(writer, "owner", owner.name);
          await pick(writer, "horse", horse.name);

          await Promise.all([writer.getByTestId("save-fields").click(), completer.getByTestId("invoice-order").click()]);

          // Whichever came first, the end state is consistent: the write always lands (a blocked completion does
          // not lock the record), and a completion only ever succeeded with both required values already there.
          await expect.poll(() => testRow(`select count(*) from custom_field_values where entity_id = ${sql(line.id)}`), { timeout: 15_000 }).toBe("2");
          const status = testRow(`select status from transactions where id = ${sql(transaction.id)}`);
          expect(["draft", "completed"]).toContain(status);
          if (status === "completed") expect(storedValues(line.id)).toBe(`horse=${horse.id},owner=${owner.id}`);
        } finally {
          await writerContext.close();
          await completerContext.close();
        }
      }
    });
  });
});

test("a transaction line added later still gets its own fields (no stale definitions)", async ({ page, context }) => {
  const { transaction, url } = await draftWithLine(context);
  const item = await createItem(context, ORG_A.id, { name: unique("Later Item") });
  await page.goto(url);
  await openAddLine(page);
  await pick(page, "item_id", item.name);
  await page.getByLabel("Quantity", { exact: true }).fill("1");
  await page.getByTestId("submit-line").click();
  await expect(rows(page)).toHaveCount(2);
  await expect(page.getByTestId("line-fields-row")).toHaveCount(2);
  expect((await getTransaction(context, ORG_A.id, transaction.id)).lines).toHaveLength(2);
});
