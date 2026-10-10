import { expect, test, type Page } from "./fixtures";

import { FREDRIK, ORG_A, ORG_B, RANDOM_ORG, bffUrl, choices, createCustomer, createHorse, picker, pick, setActive, signIn, sql, testRow, unique } from "./support";

/**
 * The Horses workflows, and the first relationship UI: Owner and Stable are customers chosen
 * with the entity picker. Real browser, real stack, dedicated test database. Every test makes
 * its own uniquely named customers and horses.
 */

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

const list = `/o/${ORG_A.id}/horses`;
const horseNames = (page: Page) => page.getByTestId("horse-row").locator("td:nth-child(2)").allTextContents() // after the No. column;
const nameField = (page: Page) => page.getByLabel("Name", { exact: true });

/** A fresh owner (and optionally stable) customer in organization A. */
async function customers(context: Parameters<typeof createCustomer>[0], ...labels: string[]) {
  const made = [];
  for (const label of labels) made.push(await createCustomer(context, ORG_A.id, unique(label)));
  return made;
}

test.describe("create", () => {
  test("a horse with an owner and a stable chosen in the browser persists in PostgreSQL with those customers", async ({ page, context }) => {
    const [owner, stable] = await customers(context, "Owner Anna", "Stable Umea");
    const name = unique("Created Horse");

    await page.goto(`${list}/new`);
    await nameField(page).fill(name);
    await pick(page, "owner_customer_id", owner.name);
    await pick(page, "stable_customer_id", stable.name);
    await page.getByLabel("Birth year").fill("2012");
    await page.getByLabel("Sex").selectOption("mare");
    await page.getByLabel("Breed").fill("Icelandic");
    await page.getByTestId("submit").click();

    await expect(page).toHaveURL(new RegExp(`${list}/[0-9a-f-]{36}\\?created=1$`));
    await expect(page.getByTestId("created")).toBeVisible();
    await expect(page.getByTestId("record-name")).toHaveText(name);
    expect(testRow(`select organization_id || '|' || owner_customer_id || '|' || stable_customer_id || '|' || birth_year || '|' || sex || '|' || breed || '|' || active from horses where name = ${sql(name)}`)).toBe(
      `${ORG_A.id}|${owner.id}|${stable.id}|2012|mare|Icelandic|true`,
    );

    // The saved relationships are shown by name, from the server, and survive a reload.
    await page.reload();
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(owner.name);
    await expect(picker(page, "stable_customer_id").getByRole("combobox")).toHaveValue(stable.name);
    await expect(page.getByLabel("Sex")).toHaveValue("mare");

    await page.goto(`${list}?q=${encodeURIComponent(name)}`);
    await expect(page.getByTestId("horse-owner")).toHaveText(owner.name);
    await expect(page.getByTestId("horse-stable")).toHaveText(stable.name);
  });

  test("the owner is required (the backend says so on the Owner control) and the stable is optional", async ({ page, context }) => {
    const [owner] = await customers(context, "Only Owner");
    const before = testRow(`select count(*) from horses where organization_id = ${sql(ORG_A.id)}`);

    await page.goto(`${list}/new`);
    await nameField(page).fill(unique("No Owner Yet"));
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-owner_customer_id")).toBeVisible();
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByTestId("error-stable_customer_id")).toHaveCount(0);
    await expect(page.getByTestId("form-error")).toHaveCount(0);
    expect(testRow(`select count(*) from horses where organization_id = ${sql(ORG_A.id)}`)).toBe(before);

    // Choosing only an owner is enough.
    const name = unique("Owner Only Horse");
    await nameField(page).fill(name);
    await pick(page, "owner_customer_id", owner.name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();
    expect(testRow(`select owner_customer_id || '|' || (stable_customer_id is null)::text from horses where name = ${sql(name)}`)).toBe(`${owner.id}|true`);
  });

  test("owner and stable can be two different customers, or the same one", async ({ page, context }) => {
    const [anna, club] = await customers(context, "Both Anna", "Both Club");
    const different = unique("Different Horse");
    const same = unique("Same Horse");

    await page.goto(`${list}/new`);
    await nameField(page).fill(different);
    await pick(page, "owner_customer_id", anna.name);
    await pick(page, "stable_customer_id", club.name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();
    expect(testRow(`select (owner_customer_id <> stable_customer_id)::text from horses where name = ${sql(different)}`)).toBe("true");

    await page.goto(`${list}/new`);
    await nameField(page).fill(same);
    await pick(page, "owner_customer_id", anna.name);
    await pick(page, "stable_customer_id", anna.name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible(); // the backend allows an owner who also keeps the horse
    expect(testRow(`select (owner_customer_id = stable_customer_id)::text from horses where name = ${sql(same)}`)).toBe("true");
  });

  test("can be created inactive", async ({ page, context }) => {
    const [owner] = await customers(context, "Inactive Horse Owner");
    const name = unique("Horse Inactive");

    await page.goto(`${list}/new`);
    await nameField(page).fill(name);
    await pick(page, "owner_customer_id", owner.name);
    await page.getByLabel("Active", { exact: true }).uncheck();
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("status")).toHaveText("Inactive");
    expect(testRow(`select active::text from horses where name = ${sql(name)}`)).toBe("false");
  });

  test("blank birth year, sex and breed are stored as no value", async ({ page, context }) => {
    const [owner] = await customers(context, "Blank Owner");
    const name = unique("Blank Horse");

    await page.goto(`${list}/new`);
    await nameField(page).fill(name);
    await pick(page, "owner_customer_id", owner.name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();

    expect(testRow(`select (birth_year is null)::text || '|' || (sex is null)::text || '|' || (breed is null)::text from horses where name = ${sql(name)}`)).toBe("true|true|true");
  });

  test("what the backend decides (year range, name, breed) shows on the matching controls and nothing is created", async ({ page, context }) => {
    const [owner] = await customers(context, "Validation Owner");
    const before = testRow(`select count(*) from horses where organization_id = ${sql(ORG_A.id)}`);

    await page.goto(`${list}/new`);
    await pick(page, "owner_customer_id", owner.name);
    await page.getByLabel("Birth year").fill("1850"); // before the earliest year; name left empty
    await page.getByLabel("Breed").fill("B".repeat(101));
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-name")).toBeVisible();
    await expect(page.getByTestId("error-birth_year")).toBeVisible();
    await expect(page.getByTestId("error-breed")).toBeVisible();
    await expect(page.getByTestId("error-owner_customer_id")).toHaveCount(0);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(owner.name); // the draft survives

    await nameField(page).fill(unique("Future Horse"));
    await page.getByLabel("Breed").fill("");
    await page.getByLabel("Birth year").fill("2999"); // in the future
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("error-birth_year")).toBeVisible();
    await expect(page.getByTestId("error-name")).toHaveCount(0);
    expect(testRow(`select count(*) from horses where organization_id = ${sql(ORG_A.id)}`)).toBe(before);
  });

  test("a birth year that is not a whole number is stopped before any request", async ({ page, context }) => {
    const [owner] = await customers(context, "Shape Owner");
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST") posts.push(request.url());
    });

    await page.goto(`${list}/new`);
    await nameField(page).fill(unique("Shape Horse"));
    await pick(page, "owner_customer_id", owner.name);
    for (const typed of ["abc", "2012.5", "-4"]) {
      await page.getByLabel("Birth year").fill(typed);
      await page.getByTestId("submit").click();
      await expect(page.getByTestId("error-birth_year")).toContainText("Enter a whole number");
    }
    expect(posts).toEqual([]);
  });

  test("the birth year goes over the wire as a JSON integer", async ({ page, context }) => {
    const [owner] = await customers(context, "Wire Owner");
    await page.goto(`${list}/new`);
    await nameField(page).fill(unique("Wire Horse"));
    await pick(page, "owner_customer_id", owner.name);
    await page.getByLabel("Birth year").fill("2012");

    const request = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/api/o/${ORG_A.id}/horses`));
    await page.getByTestId("submit").click();
    const sent = (await request).postData() ?? "";

    expect(sent).toContain('"birth_year":2012');
    expect(sent).toContain(`"owner_customer_id":"${owner.id}"`);
    expect(sent).not.toContain("organization");
  });

  test("pressing the button twice quickly creates one horse", async ({ page, context }) => {
    const [owner] = await customers(context, "Double Owner");
    const name = unique("Double Horse");
    await page.goto(`${list}/new`);
    await nameField(page).fill(name);
    await pick(page, "owner_customer_id", owner.name);

    await page.getByTestId("submit").dblclick();
    await expect(page.getByTestId("record-name")).toHaveText(name);

    expect(testRow(`select count(*) from horses where name = ${sql(name)}`)).toBe("1");
  });
});

test.describe("the picker", () => {
  test("offers only active customers for a new assignment", async ({ page, context }) => {
    const prefix = unique("Pickable");
    const active = await createCustomer(context, ORG_A.id, `${prefix} Active`);
    const inactive = await createCustomer(context, ORG_A.id, `${prefix} Inactive`);
    await setActive(context, ORG_A.id, "customers", inactive.id, false);

    await page.goto(`${list}/new`);
    for (const field of ["owner_customer_id", "stable_customer_id"]) {
      await picker(page, field).getByRole("combobox").fill(prefix);
      await expect(picker(page, field).getByRole("option")).toHaveCount(1);
      expect(await choices(page, field)).toEqual([expect.stringContaining(active.name)]);
      await picker(page, field).getByRole("combobox").fill(inactive.name);
      await expect(picker(page, field).getByText("No matches")).toBeVisible();
    }
  });

  test("works with the keyboard: type, arrow, Enter chooses without submitting the form; Escape closes", async ({ page, context }) => {
    const prefix = unique("Keys");
    await createCustomer(context, ORG_A.id, `${prefix} Alpha`);
    const beta = await createCustomer(context, ORG_A.id, `${prefix} Beta`);
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST") posts.push(request.url());
    });

    await page.goto(`${list}/new`);
    const input = picker(page, "owner_customer_id").getByRole("combobox");
    await input.click();
    await input.fill(prefix);
    await expect(picker(page, "owner_customer_id").getByRole("option")).toHaveCount(2);
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");

    await expect(input).toHaveValue(beta.name);
    expect(posts).toEqual([]); // choosing with Enter is not a form submission
    await expect(input).toHaveAttribute("aria-expanded", "false");

    await input.fill("zzz nothing");
    await page.keyboard.press("Escape");
    await expect(input).toHaveValue(beta.name); // typed text discarded, selection kept
  });

  test("typing a customer's name without choosing it assigns nobody", async ({ page, context }) => {
    const [owner] = await customers(context, "Typed Not Chosen");
    await page.goto(`${list}/new`);
    await nameField(page).fill(unique("Untyped Owner Horse"));
    await picker(page, "owner_customer_id").getByRole("combobox").fill(owner.name);
    await page.getByLabel("Birth year").click(); // leave the picker without choosing

    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-owner_customer_id")).toBeVisible();
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue("");
  });

  test("an optional stable can be cleared and the horse is saved without one", async ({ page, context }) => {
    const [owner, stable] = await customers(context, "Clear Owner", "Clear Stable");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Clear Horse"), owner_customer_id: owner.id, stable_customer_id: stable.id });

    await page.goto(`${list}/${horse.id}`);
    await picker(page, "stable_customer_id").getByRole("button", { name: "Clear Stable" }).click();
    await expect(picker(page, "stable_customer_id").getByRole("combobox")).toHaveValue("");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("saved")).toBeVisible();
    expect(testRow(`select (stable_customer_id is null)::text from horses where id = ${sql(horse.id)}`)).toBe("true");
    await expect(picker(page, "owner_customer_id").getByRole("button", { name: /Clear/ })).toHaveCount(0); // the owner is required: no Clear
  });
});

test.describe("inactive customers", () => {
  test("an existing horse still shows an owner that was deactivated later, in the form and in the list", async ({ page, context }) => {
    const [owner, stable] = await customers(context, "Later Inactive Owner", "Later Stable");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Orphaned Horse"), owner_customer_id: owner.id, stable_customer_id: stable.id });
    await setActive(context, ORG_A.id, "customers", owner.id, false);

    await page.goto(`${list}/${horse.id}`);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(`${owner.name} (inactive)`);
    await expect(picker(page, "stable_customer_id").getByRole("combobox")).toHaveValue(stable.name);

    await page.goto(`${list}?q=${encodeURIComponent(horse.name)}`);
    await expect(page.getByTestId("horse-owner")).toContainText(owner.name);
    await expect(page.getByTestId("horse-owner")).toContainText("(inactive)");
  });

  test("editing another field without touching that owner succeeds, and the owner stays", async ({ page, context }) => {
    const [owner] = await customers(context, "Stays Inactive Owner");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Edit Beside Inactive"), owner_customer_id: owner.id });
    await setActive(context, ORG_A.id, "customers", owner.id, false);

    await page.goto(`${list}/${horse.id}`);
    await page.getByLabel("Breed").fill("Fjord");
    const request = page.waitForRequest((r) => r.method() === "PATCH");
    await page.getByTestId("submit").click();

    expect((await request).postData()).toBe('{"breed":"Fjord"}'); // the unchanged owner is not sent
    await expect(page.getByTestId("saved")).toBeVisible();
    await expect(page.getByTestId("form-error")).toHaveCount(0);
    expect(testRow(`select breed || '|' || owner_customer_id from horses where id = ${sql(horse.id)}`)).toBe(`Fjord|${owner.id}`);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(`${owner.name} (inactive)`);
  });

  test("deliberately changing the owner to a customer that has become inactive is refused on the Owner control", async ({ page, context }) => {
    const [owner, candidate] = await customers(context, "Current Owner", "Candidate Owner");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Refused Reassign"), owner_customer_id: owner.id });

    await page.goto(`${list}/${horse.id}`);
    await pick(page, "owner_customer_id", candidate.name); // offered: active when the list was loaded
    await setActive(context, ORG_A.id, "customers", candidate.id, false); // ...inactive by the time of saving
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-owner_customer_id")).toContainText("inactive");
    await expect(page.getByTestId("error-stable_customer_id")).toHaveCount(0);
    await expect(page.getByTestId("form-error")).toHaveCount(0);
    expect(testRow(`select owner_customer_id from horses where id = ${sql(horse.id)}`)).toBe(owner.id);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(candidate.name); // draft kept to change
  });

  test("the same goes for a stable that became inactive before the horse was created", async ({ page, context }) => {
    const [owner, stable] = await customers(context, "Create Owner", "Create Stable");
    await page.goto(`${list}/new`);
    const name = unique("Refused Stable");
    await nameField(page).fill(name);
    await pick(page, "owner_customer_id", owner.name);
    await pick(page, "stable_customer_id", stable.name);
    await setActive(context, ORG_A.id, "customers", stable.id, false);

    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-stable_customer_id")).toContainText("inactive");
    await expect(page.getByTestId("error-owner_customer_id")).toHaveCount(0);
    expect(testRow(`select count(*) from horses where name = ${sql(name)}`)).toBe("0");
  });

  test("an owner can be reactivated, and then is offered again", async ({ page, context }) => {
    const [owner] = await customers(context, "Back Again Owner");
    await setActive(context, ORG_A.id, "customers", owner.id, false);
    await page.goto(`${list}/new`);
    await picker(page, "owner_customer_id").getByRole("combobox").fill(owner.name);
    await expect(picker(page, "owner_customer_id").getByText("No matches")).toBeVisible();

    await setActive(context, ORG_A.id, "customers", owner.id, true);
    await page.goto(`${list}/new`);
    await picker(page, "owner_customer_id").getByRole("combobox").fill(owner.name);
    await expect(picker(page, "owner_customer_id").getByRole("option")).toHaveCount(1);
  });
});

test.describe("references to customers of other organizations", () => {
  test("a customer of another organization, and a random id, are assigned to nobody and refused identically", async ({ context }) => {
    const [mine] = await customers(context, "Reference Owner");
    const foreign = await createCustomer(context, ORG_B.id, unique("Foreign Customer"));
    const before = testRow(`select count(*) from horses where organization_id = ${sql(ORG_A.id)}`);

    const attempt = async (id: string, field: "owner_customer_id" | "stable_customer_id") => {
      const data = { name: unique("Smuggled"), owner_customer_id: field === "owner_customer_id" ? id : mine.id, stable_customer_id: field === "stable_customer_id" ? id : null };
      const response = await context.request.post(bffUrl(ORG_A.id, "/horses"), { data });
      return { status: response.status(), body: await response.text() };
    };

    for (const field of ["owner_customer_id", "stable_customer_id"] as const) {
      const viaForeign = await attempt(foreign.id, field);
      const viaRandom = await attempt(RANDOM_ORG, field);
      expect(viaForeign.status).toBe(422);
      expect(viaForeign).toEqual(viaRandom);
      expect(viaForeign.body).toContain(field);
      expect(viaForeign.body).not.toContain(foreign.name);
    }
    expect(testRow(`select count(*) from horses where organization_id = ${sql(ORG_A.id)}`)).toBe(before);
  });

  test("the same on edit: neither a foreign nor a random customer can be assigned to an existing horse", async ({ context }) => {
    const [mine] = await customers(context, "Edit Reference Owner");
    const foreign = await createCustomer(context, ORG_B.id, unique("Foreign For Edit"));
    const horse = await createHorse(context, ORG_A.id, { name: unique("Edit Reference Horse"), owner_customer_id: mine.id });

    for (const field of ["owner_customer_id", "stable_customer_id"]) {
      const viaForeign = await context.request.patch(bffUrl(ORG_A.id, `/horses/${horse.id}`), { data: { [field]: foreign.id } });
      const viaRandom = await context.request.patch(bffUrl(ORG_A.id, `/horses/${horse.id}`), { data: { [field]: RANDOM_ORG } });
      expect(viaForeign.status()).toBe(422);
      expect({ status: viaForeign.status(), body: await viaForeign.text() }).toEqual({ status: viaRandom.status(), body: await viaRandom.text() });
    }
    expect(testRow(`select owner_customer_id || '|' || (stable_customer_id is null)::text from horses where id = ${sql(horse.id)}`)).toBe(`${mine.id}|true`);
  });

  test("another organization's customers are never offered by the picker", async ({ page, context }) => {
    const foreignName = unique("Only In Other Org");
    await createCustomer(context, ORG_B.id, foreignName);

    await page.goto(`${list}/new`);
    await picker(page, "owner_customer_id").getByRole("combobox").fill(foreignName);
    await expect(picker(page, "owner_customer_id").getByText("No matches")).toBeVisible();
  });
});

test.describe("edit, deactivate and reactivate", () => {
  test("changes persist, the owner can be changed, and untouched fields stay", async ({ page, context }) => {
    const [first, second] = await customers(context, "Edit First Owner", "Edit Second Owner");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Before"), owner_customer_id: first.id, birth_year: 2010, breed: "Keep" });
    const renamed = unique("After");

    await page.goto(`${list}/${horse.id}`);
    await nameField(page).fill(renamed);
    await pick(page, "owner_customer_id", second.name);
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("saved")).toBeVisible();
    await expect(page.getByTestId("record-name")).toHaveText(renamed);
    expect(testRow(`select name || '|' || owner_customer_id || '|' || birth_year || '|' || breed from horses where id = ${sql(horse.id)}`)).toBe(`${renamed}|${second.id}|2010|Keep`);
    await page.reload();
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(second.name);
  });

  test("a rejected edit keeps the draft and leaves the stored horse alone", async ({ page, context }) => {
    const [owner] = await customers(context, "Rejected Edit Owner");
    const name = unique("Rejected Edit");
    const horse = await createHorse(context, ORG_A.id, { name, owner_customer_id: owner.id });

    await page.goto(`${list}/${horse.id}`);
    await page.getByLabel("Birth year").fill("1850");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("error-birth_year")).toBeVisible();
    await expect(page.getByLabel("Birth year")).toHaveValue("1850");
    expect(testRow(`select name || '|' || (birth_year is null)::text from horses where id = ${sql(horse.id)}`)).toBe(`${name}|true`);
  });

  test("deactivating and reactivating persists, leaves the owner alone, and the list filter follows", async ({ page, context }) => {
    const [owner] = await customers(context, "Toggle Owner");
    const name = unique("Toggle Horse");
    const horse = await createHorse(context, ORG_A.id, { name, owner_customer_id: owner.id });

    await page.goto(`${list}/${horse.id}`);
    await page.getByRole("button", { name: "Deactivate horse" }).click();
    await expect(page.getByTestId("status")).toHaveText("Inactive");
    expect(testRow(`select active::text || '|' || owner_customer_id from horses where id = ${sql(horse.id)}`)).toBe(`false|${owner.id}`);

    await page.goto(`${list}?q=${encodeURIComponent(name)}&active=active`);
    await expect(page.getByTestId("empty")).toBeVisible();
    await page.goto(`${list}?q=${encodeURIComponent(name)}&active=inactive`);
    await expect(page.getByTestId("horse-row")).toHaveCount(1);

    await page.getByRole("link", { name }).click();
    await page.getByRole("button", { name: "Reactivate horse" }).click();
    await expect(page.getByTestId("status")).toHaveText("Active");
    expect(testRow(`select active::text from horses where id = ${sql(horse.id)}`)).toBe("true");
  });

  test("a horse can be deactivated while its owner is inactive", async ({ page, context }) => {
    const [owner] = await customers(context, "Inactive Toggle Owner");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Toggle Beside Inactive"), owner_customer_id: owner.id });
    await setActive(context, ORG_A.id, "customers", owner.id, false);

    await page.goto(`${list}/${horse.id}`);
    await page.getByRole("button", { name: "Deactivate horse" }).click();

    await expect(page.getByTestId("status")).toHaveText("Inactive");
    await expect(page.getByTestId("form-error")).toHaveCount(0);
    await page.getByRole("button", { name: "Reactivate horse" }).click();
    await expect(page.getByTestId("status")).toHaveText("Active");
  });
});

test.describe("list, search and filters", () => {
  test("search and the Owner and Stable filters combine, and the filter pickers keep the choice in the address", async ({ page, context }) => {
    const prefix = unique("HFilt");
    const [anna, bea, club] = await customers(context, `${prefix} Anna`, `${prefix} Bea`, `${prefix} Club`);
    await createHorse(context, ORG_A.id, { name: `${prefix} Alpha`, owner_customer_id: anna.id, stable_customer_id: club.id });
    await createHorse(context, ORG_A.id, { name: `${prefix} Beta`, owner_customer_id: bea.id, stable_customer_id: club.id });
    await createHorse(context, ORG_A.id, { name: `${prefix} Gamma`, owner_customer_id: anna.id });

    await page.goto(`${list}?q=${encodeURIComponent(prefix)}`);
    expect((await horseNames(page)).sort()).toEqual([`${prefix} Alpha`, `${prefix} Beta`, `${prefix} Gamma`]);

    // By owner, chosen with the filter picker.
    await pick(page, "owner_customer_id", anna.name);
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page).toHaveURL(new RegExp(`owner_customer_id=${anna.id}`));
    expect((await horseNames(page)).sort()).toEqual([`${prefix} Alpha`, `${prefix} Gamma`]);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(anna.name);

    // And by stable on top of it.
    await pick(page, "stable_customer_id", club.name);
    await page.getByRole("button", { name: "Apply" }).click();
    expect(await horseNames(page)).toEqual([`${prefix} Alpha`]);

    // Clearing a filter and applying widens the list again.
    await picker(page, "owner_customer_id").getByRole("button", { name: "Clear Owner" }).click();
    await page.getByRole("button", { name: "Apply" }).click();
    expect((await horseNames(page)).sort()).toEqual([`${prefix} Alpha`, `${prefix} Beta`]);
    expect(page.url()).not.toMatch(/owner_customer_id=[0-9a-f]/); // a GET form sends the cleared field empty; the page treats that as no filter
  });

  test("the filter pickers also find a customer that has been deactivated, so their horses stay findable", async ({ page, context }) => {
    const [owner] = await customers(context, "Filter Inactive Owner");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Filter Inactive Horse"), owner_customer_id: owner.id });
    await setActive(context, ORG_A.id, "customers", owner.id, false);

    await page.goto(list);
    await picker(page, "owner_customer_id").getByRole("combobox").fill(owner.name);
    await expect(picker(page, "owner_customer_id").getByRole("option")).toHaveCount(1);
    await picker(page, "owner_customer_id").getByRole("option").first().click();
    await page.getByRole("button", { name: "Apply" }).click();

    expect(await horseNames(page)).toEqual([horse.name]);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(`${owner.name} (inactive)`);
  });

  test("a foreign or random customer id in the address matches nothing and is shown the same way", async ({ page, context }) => {
    const foreign = await createCustomer(context, ORG_B.id, unique("Foreign Filter"));

    const outcome = async (id: string) => {
      await page.goto(`${list}?owner_customer_id=${id}`);
      return {
        rows: await page.getByTestId("horse-row").count(),
        empty: await page.getByTestId("empty").textContent(),
        picker: await picker(page, "owner_customer_id").getByRole("combobox").inputValue(),
        text: await page.locator("main").innerText(),
      };
    };
    const viaForeign = await outcome(foreign.id);
    const viaRandom = await outcome(RANDOM_ORG);

    expect(viaForeign.rows).toBe(0);
    expect(viaForeign.picker).toBe("Unknown customer");
    expect({ ...viaForeign, text: viaForeign.text.replaceAll(foreign.id, "ID") }).toEqual({ ...viaRandom, text: viaRandom.text.replaceAll(RANDOM_ORG, "ID") });
    expect(viaForeign.text).not.toContain(foreign.name);
  });

  test("a malformed or hostile filter value is ignored, not forwarded", async ({ page }) => {
    const response = await page.goto(`${list}?owner_customer_id=not-a-uuid&stable_customer_id=%27%3B%20drop%20table%20horses%3B--&organization_id=${ORG_A.id}`);
    expect(response?.status()).toBe(200);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue("");
    expect(testRow("select (count(*) > 0)::text from horses")).toBe("true");
  });

  test("the seeded horse Kalle is listed with the owner Anna Andersson and the stable Umeå HK", async ({ page }) => {
    await page.goto(`${list}?q=Kalle`);
    await expect(page.getByTestId("horse-row")).toHaveCount(1);
    await expect(page.getByTestId("horse-owner")).toHaveText("Anna Andersson");
    await expect(page.getByTestId("horse-stable")).toHaveText("Umeå HK");
  });
});

test.describe("navigation and history", () => {
  test("the main navigation reaches Horses, and the list links to the form, the horse and its owner", async ({ page, context }) => {
    const [owner] = await customers(context, "Nav Owner");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Nav Horse"), owner_customer_id: owner.id });

    await page.goto(`/o/${ORG_A.id}`);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Horses" }).click();
    await expect(page).toHaveURL(list);
    await page.getByTestId("new-horse").click();
    await expect(page).toHaveURL(`${list}/new`);
    await page.getByRole("link", { name: "Cancel" }).click();

    await page.goto(`${list}?q=${encodeURIComponent(horse.name)}`);
    await page.getByTestId("horse-owner").getByRole("link").click();
    await expect(page).toHaveURL(`/o/${ORG_A.id}/customers/${owner.id}`);
  });

  test("browser Back to a list visited before a create shows the new horse", async ({ page, context }) => {
    const [owner] = await customers(context, "Back Owner");
    const name = unique("Back Horse");
    const query = `?q=${encodeURIComponent("Back Horse")}`;

    await page.goto(`${list}${query}`);
    await page.getByTestId("new-horse").click();
    await nameField(page).fill(name);
    await pick(page, "owner_customer_id", owner.name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("created")).toBeVisible();

    await page.goBack();
    await page.goBack();

    await expect(page).toHaveURL(`${list}${query}`);
    await expect(page.getByRole("link", { name })).toBeVisible();
  });

  test("the list shows a saved owner change after browser Back", async ({ page, context }) => {
    const [first, second] = await customers(context, "Back First Owner", "Back Second Owner");
    const horse = await createHorse(context, ORG_A.id, { name: unique("Back Edit Horse"), owner_customer_id: first.id });
    const query = `?q=${encodeURIComponent(horse.name)}`;

    await page.goto(`${list}${query}`);
    await expect(page.getByTestId("horse-owner")).toHaveText(first.name);
    await page.getByRole("link", { name: horse.name }).click();
    // The list's Owner filter is a picker with the same field name: wait until the horse's own form is shown.
    await expect(page.getByTestId("record-name")).toHaveText(horse.name);
    await pick(page, "owner_customer_id", second.name);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();

    await page.goBack();
    await expect(page.getByTestId("horse-owner")).toHaveText(second.name);
  });
});
