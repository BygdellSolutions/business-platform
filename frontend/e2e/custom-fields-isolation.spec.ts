import { expect, test, type APIResponse, type Page } from "./fixtures";

import {
  FREDRIK,
  MARIA,
  ORG_A,
  ORG_B,
  RANDOM_ORG,
  addLine,
  bffUrl,
  choices,
  createCustomer,
  createHorse,
  createTransaction,
  definitionsOf,
  idOf,
  picker,
  pick,
  signIn,
  sql,
  storedValues,
  testRow,
  unique,
  uniqueKey,
  withDefinitions,
} from "./support";

/**
 * Tenant isolation for custom fields. Both organizations have the SAME seeded Anna Andersson and
 * Kalle (different ids) and the same Owner/Horse field definitions, so a mix-up of a definition, a
 * value, a choice or an id shows in the page text, the submitted UUID or the database.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const txUrl = (orgId: string, id: string) => `/o/${orgId}/transactions/${id}`;
const linePanel = (page: Page) => page.locator('[data-testid="custom-fields"][data-entity-type="transaction_line"]').first();
const ownerBox = (page: Page) => picker(page, "owner").getByRole("combobox");
const horseBox = (page: Page) => picker(page, "horse").getByRole("combobox");
const switchTo = (page: Page, target: { name: string }) => page.getByTestId("org-switcher").getByRole("link", { name: target.name }).click();

async function outcome(response: APIResponse) {
  return { status: response.status(), body: await response.text() };
}

/** A draft transaction with one ad-hoc line in the given organization. */
async function draftWithLine(context: Parameters<typeof createCustomer>[0], orgId: string) {
  const customer = await createCustomer(context, orgId, unique("Iso Billing"));
  const transaction = await createTransaction(context, orgId, { billing_customer_id: customer.id, transaction_date: "2026-10-01" });
  const line = await addLine(context, orgId, transaction.id, { description: "Iso line", unit: "u", quantity: "1", unit_price_ex_vat: "10.00", vat_rate: "25" });
  return { transaction, line, url: txUrl(orgId, transaction.id) };
}

const anna = (orgId: string) => idOf("customers", orgId, "Anna Andersson");
const kalle = (orgId: string) => idOf("horses", orgId, "Kalle");

test.describe("identical-looking Anna and Kalle in two organizations never mix", () => {
  test("choosing them in each organization stores that organization's own ids", async ({ page, context }) => {
    expect(anna(ORG_A.id)).not.toBe(anna(ORG_B.id));
    expect(kalle(ORG_A.id)).not.toBe(kalle(ORG_B.id));

    for (const current of [ORG_A, ORG_B]) {
      const { line, url } = await draftWithLine(context, current.id);
      await page.goto(url);
      await expect(page.getByTestId("org-name")).toHaveText(current.name);
      await page.getByTestId("edit-fields").click();

      await ownerBox(page).click();
      await ownerBox(page).fill("Anna Andersson");
      await expect(picker(page, "owner").getByRole("option").filter({ hasText: /^Anna Andersson/ })).toHaveCount(1); // never two
      await picker(page, "owner").getByRole("option").filter({ hasText: /^Anna Andersson/ }).click();
      await horseBox(page).click();
      await expect(picker(page, "horse").getByRole("option").filter({ hasText: "Kalle" })).toHaveCount(1);
      await picker(page, "horse").getByRole("option").filter({ hasText: "Kalle" }).click();
      await page.getByTestId("save-fields").click();
      await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Kalle");

      expect(storedValues(line.id)).toBe(`horse=${kalle(current.id)},owner=${anna(current.id)}`);
      const other = current.id === ORG_A.id ? ORG_B.id : ORG_A.id;
      expect(storedValues(line.id)).not.toContain(anna(other));
      expect(storedValues(line.id)).not.toContain(kalle(other));
    }
  });

  test("an organization's own records are the only choices, for the owner and for the dependent horse", async ({ page, context }) => {
    const onlyA = await createCustomer(context, ORG_A.id, unique("Only In A Owner"));
    const onlyAHorse = await createHorse(context, ORG_A.id, { name: unique("Only In A Horse"), owner_customer_id: onlyA.id });
    const onlyB = await createCustomer(context, ORG_B.id, unique("Only In B Owner"));
    const { url: urlB } = await draftWithLine(context, ORG_B.id);
    const { url: urlA } = await draftWithLine(context, ORG_A.id);

    await page.goto(urlB);
    await page.getByTestId("edit-fields").click();
    await ownerBox(page).fill(onlyA.name);
    await expect(picker(page, "owner").getByText("No matches")).toBeVisible(); // A's customer is not offered in B
    await ownerBox(page).fill(onlyB.name);
    await expect(picker(page, "owner").getByRole("option")).toHaveCount(1);

    await page.goto(urlA);
    await page.getByTestId("edit-fields").click();
    await ownerBox(page).fill(onlyB.name);
    await expect(picker(page, "owner").getByText("No matches")).toBeVisible();
    await ownerBox(page).fill(onlyA.name);
    await picker(page, "owner").getByRole("option").first().click();
    await horseBox(page).click();
    await expect(picker(page, "horse").getByRole("option").filter({ hasText: onlyAHorse.name })).toHaveCount(1);
  });
});

test.describe("forging it through the BFF", () => {
  test("a horse that is not the owner's, and a horse without an owner, are refused on the Horse field and store nothing", async ({ context }) => {
    const { line } = await draftWithLine(context, ORG_A.id);
    const erik = await createCustomer(context, ORG_A.id, unique("Erik Forged"));
    const url = bffUrl(ORG_A.id, `/custom-fields/entities/transaction_line/${line.id}/values`);

    const wrongPair = await context.request.patch(url, { data: { values: { owner: erik.id, horse: kalle(ORG_A.id) } } }); // Kalle is Anna's
    expect(wrongPair.status()).toBe(422);
    expect(await wrongPair.text()).toContain("values");
    expect(JSON.stringify(await wrongPair.json())).toContain("not one of the choices for the selected Owner");

    const orphan = await context.request.patch(url, { data: { values: { horse: kalle(ORG_A.id) } } });
    expect(orphan.status()).toBe(422);
    expect(JSON.stringify(await orphan.json())).toContain("Set Owner first");

    expect(storedValues(line.id)).toBe("");
  });

  test("a customer or horse of another organization is refused exactly like a random id, naming nothing", async ({ context }) => {
    const { line } = await draftWithLine(context, ORG_A.id);
    const url = bffUrl(ORG_A.id, `/custom-fields/entities/transaction_line/${line.id}/values`);
    const attempt = async (values: Record<string, string>) => outcome(await context.request.patch(url, { data: { values } }));

    const ownerForeign = await attempt({ owner: anna(ORG_B.id) });
    const ownerRandom = await attempt({ owner: RANDOM_ORG });
    expect(ownerForeign.status).toBe(422);
    expect(ownerForeign).toEqual(ownerRandom);
    expect(ownerForeign.body).not.toContain("Anna");

    const horseForeign = await attempt({ owner: anna(ORG_A.id), horse: kalle(ORG_B.id) });
    const horseRandom = await attempt({ owner: anna(ORG_A.id), horse: RANDOM_ORG });
    expect(horseForeign.status).toBe(422);
    expect(horseForeign).toEqual(horseRandom);
    expect(horseForeign.body).not.toContain("Kalle");
    expect(storedValues(line.id)).toBe("");
  });

  test("the choices endpoint cannot be used to look into another organization", async ({ context }) => {
    const definitionsA = await definitionsOf(context, ORG_A.id, "transaction_line");
    const definitionsB = await definitionsOf(context, ORG_B.id, "transaction_line");
    const horseA = definitionsA.find((definition) => definition.key === "horse")!;
    const horseB = definitionsB.find((definition) => definition.key === "horse")!;
    expect(definitionsA.map((definition) => definition.id)).not.toEqual(expect.arrayContaining([horseB.id]));

    // B's owner id as the dependency, asked in A: matches nothing, the same as a random id.
    const viaForeignParent = await outcome(await context.request.get(bffUrl(ORG_A.id, `/custom-fields/definitions/${horseA.id}/choices?depends_on_value=${anna(ORG_B.id)}`)));
    const viaRandomParent = await outcome(await context.request.get(bffUrl(ORG_A.id, `/custom-fields/definitions/${horseA.id}/choices?depends_on_value=${RANDOM_ORG}`)));
    expect(viaForeignParent).toEqual({ status: 200, body: "[]" });
    expect(viaForeignParent).toEqual(viaRandomParent);

    // B's definition asked through A's address is a 404, the same as a random definition.
    const viaForeignDefinition = await outcome(await context.request.get(bffUrl(ORG_A.id, `/custom-fields/definitions/${horseB.id}/choices?depends_on_value=${anna(ORG_A.id)}`)));
    const viaRandomDefinition = await outcome(await context.request.get(bffUrl(ORG_A.id, `/custom-fields/definitions/${RANDOM_ORG}/choices?depends_on_value=${anna(ORG_A.id)}`)));
    expect(viaForeignDefinition.status).toBe(404);
    expect(viaForeignDefinition).toEqual(viaRandomDefinition);
  });

  test("another organization's line is a 404 for reading and writing values, like a random id, and nothing changes", async ({ context }) => {
    const { line: lineB } = await draftWithLine(context, ORG_B.id);
    const before = storedValues(lineB.id);
    for (const [method, data] of [["get", undefined], ["patch", { values: { owner: anna(ORG_A.id) } }]] as const) {
      const viaA = await outcome(await context.request[method](bffUrl(ORG_A.id, `/custom-fields/entities/transaction_line/${lineB.id}/values`), { data }));
      const random = await outcome(await context.request[method](bffUrl(ORG_A.id, `/custom-fields/entities/transaction_line/${RANDOM_ORG}/values`), { data }));
      expect(viaA.status).toBe(404);
      expect(viaA).toEqual(random);
    }
    expect(storedValues(lineB.id)).toBe(before);
  });

  test("a member of B only cannot read or write A's values through either address", async ({ context }) => {
    const { line } = await draftWithLine(context, ORG_A.id);
    await context.clearCookies();
    await signIn(context, MARIA);
    for (const orgId of [ORG_A.id, ORG_B.id]) {
      const read = await context.request.get(bffUrl(orgId, `/custom-fields/entities/transaction_line/${line.id}/values`));
      const write = await context.request.patch(bffUrl(orgId, `/custom-fields/entities/transaction_line/${line.id}/values`), { data: { values: { owner: anna(orgId) } } });
      expect([read.status(), write.status()]).toEqual([404, 404]);
    }
    expect(storedValues(line.id)).toBe("");
  });

  test("forged organization or identity headers change nothing about where a value is written", async ({ context }) => {
    const { line } = await draftWithLine(context, ORG_A.id);
    const forged = { "x-organization-id": ORG_B.id, "x-dev-user-email": MARIA, authorization: "Bearer forged" };

    const write = await context.request.patch(bffUrl(ORG_A.id, `/custom-fields/entities/transaction_line/${line.id}/values`), { headers: forged, data: { values: { owner: anna(ORG_A.id) } } });
    expect(write.status()).toBe(200);
    expect(storedValues(line.id)).toBe(`owner=${anna(ORG_A.id)}`);

    const smuggled = await context.request.patch(bffUrl(ORG_A.id, `/custom-fields/entities/transaction_line/${line.id}/values`), { headers: forged, data: { values: { owner: anna(ORG_B.id) } } });
    expect(smuggled.status()).toBe(422); // B's Anna is foreign to the REAL organization of the request
    expect(storedValues(line.id)).toBe(`owner=${anna(ORG_A.id)}`);
  });
});

test.describe("switching organizations", () => {
  test("definitions, values, drafts and choices of the previous organization do not survive", async ({ page, context }) => {
    const draftOnlyInA = await createCustomer(context, ORG_A.id, unique("Draft Only In A"));
    const { line: lineA, url: urlA } = await draftWithLine(context, ORG_A.id);
    const { line: lineB, url: urlB } = await draftWithLine(context, ORG_B.id);
    await context.request.patch(bffUrl(ORG_B.id, `/custom-fields/entities/transaction_line/${lineB.id}/values`), { data: { values: { owner: anna(ORG_B.id), horse: kalle(ORG_B.id) } } });
    const key = uniqueKey("onlya");

    await withDefinitions(context, ORG_A.id, [{ entity_type: "transaction_line", key, label: "Field Only In A", field_type: "text" }], async () => {
      await page.goto(urlA);
      await expect(page.getByText("Field Only In A")).toBeVisible(); // A's definition
      await page.getByTestId("edit-fields").click();
      await pick(page, "owner", draftOnlyInA.name); // a draft with a chosen owner and the picker left open
      await ownerBox(page).click();

      await switchTo(page, ORG_B);
      await expect(page).toHaveURL(`/o/${ORG_B.id}`);
      await page.goto(urlB);

      await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
      await expect(page.getByText("Field Only In A")).toHaveCount(0); // not B's definition
      await expect(page.getByTestId("custom-fields-form")).toHaveCount(0); // no draft
      await expect(linePanel(page).getByTestId("cf-owner")).toContainText("Anna Andersson"); // B's own values
      await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Kalle");
      expect(await page.content()).not.toContain(draftOnlyInA.name);
      expect(storedValues(lineA.id)).toBe(""); // the draft was never saved anywhere
      expect(storedValues(lineB.id)).toContain(`owner=${anna(ORG_B.id)}`);
    });
  });

  test("an answer to a choice request for the old organization never appears in the new one", async ({ page, context }) => {
    const onlyA = await createCustomer(context, ORG_A.id, unique("Late Answer A"));
    const { url: urlA } = await draftWithLine(context, ORG_A.id);
    const { url: urlB } = await draftWithLine(context, ORG_B.id);
    await page.route(`**/api/o/${ORG_A.id}/custom-fields/definitions/*/choices**`, async (route) => {
      const response = await route.fetch(); // the request goes out at once...
      await new Promise((resolve) => setTimeout(resolve, 2000)); // ...the answer is slow
      await route.fulfill({ response }).catch(() => {});
    });
    await page.goto(urlA);
    await page.getByTestId("edit-fields").click();
    await ownerBox(page).click(); // asks A; no answer yet
    await expect(picker(page, "owner").getByText("Searching…")).toBeVisible();

    await switchTo(page, ORG_B);
    await page.goto(urlB);
    await page.getByTestId("edit-fields").click();
    await ownerBox(page).fill("Anna");
    await expect(picker(page, "owner").getByRole("option").first()).toBeVisible();
    await page.waitForTimeout(2500); // long enough for A's answer to arrive anywhere it could

    expect((await choices(page, "owner")).join("|")).not.toContain(onlyA.name);
    expect(await page.content()).not.toContain(onlyA.name);
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
  });

  test("an answer for the OLD owner never fills the horse choices after the owner was changed", async ({ page, context }) => {
    const first = await createCustomer(context, ORG_A.id, unique("Old Parent"));
    const firstHorse = await createHorse(context, ORG_A.id, { name: unique("Horse Of Old Parent"), owner_customer_id: first.id });
    const second = await createCustomer(context, ORG_A.id, unique("New Parent"));
    const secondHorse = await createHorse(context, ORG_A.id, { name: unique("Horse Of New Parent"), owner_customer_id: second.id });
    const { url } = await draftWithLine(context, ORG_A.id);
    await page.route(`**/api/o/${ORG_A.id}/custom-fields/definitions/*/choices?*depends_on_value=${first.id}*`, async (route) => {
      const response = await route.fetch();
      await new Promise((resolve) => setTimeout(resolve, 2500)); // the OLD owner's horses arrive late
      await route.fulfill({ response }).catch(() => {});
    });
    await page.goto(url);
    await page.getByTestId("edit-fields").click();

    await pick(page, "owner", first.name);
    await horseBox(page).click(); // asks for the old owner's horses; slow
    await expect(picker(page, "horse").getByText("Searching…")).toBeVisible();
    await picker(page, "owner").getByRole("button", { name: "Clear Owner" }).click(); // the owner changes
    await pick(page, "owner", second.name);
    await horseBox(page).click();
    await expect(picker(page, "horse").getByRole("option").filter({ hasText: secondHorse.name })).toHaveCount(1);

    await page.waitForTimeout(3000); // the old answer has arrived by now
    const offered = (await choices(page, "horse")).join("|");
    expect(offered).toContain(secondHorse.name);
    expect(offered).not.toContain(firstHorse.name);
  });

  test("back and forward across a switch show each organization's own values", async ({ page, context }) => {
    const { line: lineA, url: urlA } = await draftWithLine(context, ORG_A.id);
    const { line: lineB, url: urlB } = await draftWithLine(context, ORG_B.id);
    await context.request.patch(bffUrl(ORG_A.id, `/custom-fields/entities/transaction_line/${lineA.id}/values`), { data: { values: { owner: anna(ORG_A.id) } } });
    await context.request.patch(bffUrl(ORG_B.id, `/custom-fields/entities/transaction_line/${lineB.id}/values`), { data: { values: { owner: anna(ORG_B.id), horse: kalle(ORG_B.id) } } });

    await page.goto(urlA);
    await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Not set");
    await switchTo(page, ORG_B);
    await page.goto(urlB);
    await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Kalle");

    await page.goBack();
    await page.goBack();
    await expect(page).toHaveURL(urlA);
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);
    await expect(linePanel(page).getByTestId("cf-horse")).toContainText("Not set"); // not B's Kalle
  });
});

test("after all of the above, no custom value refers to a record of another organization", async () => {
  const mismatches = [
    "select count(*) from custom_field_values v join custom_field_definitions d on d.id = v.definition_id join customers c on c.id = v.value_reference_id where d.reference_source = 'customer' and c.organization_id <> v.organization_id",
    "select count(*) from custom_field_values v join custom_field_definitions d on d.id = v.definition_id join horses h on h.id = v.value_reference_id where d.reference_source = 'horse' and h.organization_id <> v.organization_id",
    "select count(*) from custom_field_values v join custom_field_definitions d on d.id = v.definition_id where d.organization_id <> v.organization_id",
  ];
  for (const query of mismatches) expect(testRow(query)).toBe("0");
  void sql;
});
