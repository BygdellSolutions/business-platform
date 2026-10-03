import { expect, test, type APIResponse, type Page } from "./fixtures";

import { BASE_URL } from "./env";
import { FREDRIK, MARIA, ORG_A, ORG_B, RANDOM_ORG, bffUrl, choices, createCustomer, createHorse, picker, pick, signIn, sql, testRow, unique } from "./support";

/**
 * Tenant isolation for the Horses screens and the entity picker, as the browser experiences
 * it. Both organizations get a customer and a horse with the SAME names next to records that
 * exist in one organization only, so any mix-up shows in the page text, the submitted id or
 * the database.
 */

const TAG = unique("HIso");
const TWIN_OWNER = `${TAG} Twin Owner`;
const TWIN_HORSE = `${TAG} Twin Horse`;
const ONLY_A_OWNER = `${TAG} Only A Owner`;
const ONLY_B_OWNER = `${TAG} Only B Owner`;
const ONLY_A_HORSE = `${TAG} Only A Horse`;
const ONLY_B_HORSE = `${TAG} Only B Horse`;

const ids = {
  a: { owner: "", onlyOwner: "", horse: "", onlyHorse: "" },
  b: { owner: "", onlyOwner: "", horse: "", onlyHorse: "" },
};

const horses = (orgId: string, query = "") => `/o/${orgId}/horses${query}`;
const horseNames = (page: Page) => page.getByTestId("horse-row").locator("td:first-child").allTextContents();
const switchTo = (page: Page, org: { name: string }) => page.getByTestId("org-switcher").getByRole("link", { name: org.name }).click();
const ownerId = (page: Page) => page.locator('input[type="hidden"][name="owner_customer_id"]').inputValue();

async function outcome(response: APIResponse) {
  return { status: response.status(), body: await response.text() };
}

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL });
  await signIn(context, FREDRIK);
  for (const [key, org] of [["a", ORG_A], ["b", ORG_B]] as const) {
    const side = key === "a" ? "A" : "B";
    ids[key].owner = (await createCustomer(context, org.id, TWIN_OWNER)).id;
    ids[key].onlyOwner = (await createCustomer(context, org.id, `${TAG} Only ${side} Owner`)).id;
    ids[key].horse = (await createHorse(context, org.id, { name: TWIN_HORSE, owner_customer_id: ids[key].owner, birth_year: 2010 })).id;
    ids[key].onlyHorse = (await createHorse(context, org.id, { name: `${TAG} Only ${side} Horse`, owner_customer_id: ids[key].onlyOwner })).id;
  }
  await context.close();
});

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

test.describe("identical-looking customers and horses stay separate", () => {
  test("each organization lists only its own horses, whose owners link to its own customers", async ({ page }) => {
    for (const [org, own] of [[ORG_A, ids.a], [ORG_B, ids.b]] as const) {
      const side = org === ORG_A ? "A" : "B";
      await page.goto(horses(org.id, `?q=${encodeURIComponent(TAG)}`));
      await expect(page.getByTestId("org-name")).toHaveText(org.name);
      expect((await horseNames(page)).sort()).toEqual([`${TAG} Only ${side} Horse`, TWIN_HORSE]);

      const twinRow = page.getByTestId("horse-row").filter({ hasText: TWIN_HORSE });
      expect(await twinRow.getByTestId("horse-owner").getByRole("link").getAttribute("href")).toBe(`/o/${org.id}/customers/${own.owner}`);
    }
  });

  test("a twin horse's form shows and submits its own organization's owner", async ({ page }) => {
    for (const [org, own, other] of [[ORG_A, ids.a, ids.b], [ORG_B, ids.b, ids.a]] as const) {
      await page.goto(`${horses(org.id)}/${own.horse}`);
      await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue(TWIN_OWNER);
      expect(await ownerId(page)).toBe(own.owner);
      expect(await ownerId(page)).not.toBe(other.owner);
    }
  });

  test("choosing 'Twin Owner' in each organization assigns that organization's customer", async ({ page }) => {
    for (const [org, own] of [[ORG_A, ids.a], [ORG_B, ids.b]] as const) {
      const name = unique("Twin Pick Horse");
      await page.goto(`${horses(org.id)}/new`);
      await page.getByLabel("Name", { exact: true }).fill(name);
      await picker(page, "owner_customer_id").getByRole("combobox").fill(TWIN_OWNER);
      await expect(picker(page, "owner_customer_id").getByRole("option")).toHaveCount(1); // never two
      await picker(page, "owner_customer_id").getByRole("option").click();
      expect(await ownerId(page)).toBe(own.owner);
      await page.getByTestId("submit").click();
      await expect(page.getByTestId("created")).toBeVisible();

      expect(testRow(`select owner_customer_id || '|' || organization_id from horses where name = ${sql(name)}`)).toBe(`${own.owner}|${org.id}`);
    }
  });

  test("editing, reassigning or deactivating one organization's twin horse leaves the other's untouched", async ({ page, context }) => {
    const name = unique("Edit Twin Horse");
    const a = await createHorse(context, ORG_A.id, { name, owner_customer_id: ids.a.owner });
    const b = await createHorse(context, ORG_B.id, { name, owner_customer_id: ids.b.owner });

    await page.goto(`${horses(ORG_A.id)}/${a.id}`);
    await page.getByLabel("Name", { exact: true }).fill(`${name} renamed`);
    await pick(page, "owner_customer_id", ONLY_A_OWNER);
    await page.getByLabel("Breed").fill("Only in A");
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("saved")).toBeVisible();
    await page.getByRole("button", { name: "Deactivate horse" }).click();
    await expect(page.getByTestId("status")).toHaveText("Inactive");

    expect(testRow(`select name || '|' || owner_customer_id || '|' || (breed is null)::text || '|' || active::text from horses where id = ${sql(b.id)}`)).toBe(`${name}|${ids.b.owner}|true|true`);
    expect(testRow(`select owner_customer_id from horses where id = ${sql(a.id)}`)).toBe(ids.a.onlyOwner);
  });
});

test.describe("search results and picker choices contain only the organization in the URL", () => {
  test("the owner and stable pickers offer only that organization's customers", async ({ page }) => {
    for (const [org, only, foreign] of [[ORG_A, ONLY_A_OWNER, ONLY_B_OWNER], [ORG_B, ONLY_B_OWNER, ONLY_A_OWNER]] as const) {
      await page.goto(`${horses(org.id)}/new`);
      for (const field of ["owner_customer_id", "stable_customer_id"]) {
        await picker(page, field).getByRole("combobox").fill(TAG);
        await expect(picker(page, field).getByRole("option")).toHaveCount(2);
        const offered = (await choices(page, field)).join("|");
        expect(offered).toContain(only);
        expect(offered).toContain(TWIN_OWNER);
        expect(offered).not.toContain(foreign);

        await picker(page, field).getByRole("combobox").fill(foreign);
        await expect(picker(page, field).getByText("No matches")).toBeVisible();
      }
    }
  });

  test("the list search and the filter pickers (which also offer inactive customers) are scoped too", async ({ page }) => {
    await page.goto(horses(ORG_A.id, `?q=${encodeURIComponent(ONLY_B_HORSE)}`));
    await expect(page.getByTestId("empty")).toBeVisible();

    await page.goto(horses(ORG_A.id));
    for (const field of ["owner_customer_id", "stable_customer_id"]) {
      await picker(page, field).getByRole("combobox").fill(ONLY_B_OWNER);
      await expect(picker(page, field).getByText("No matches")).toBeVisible();
      await picker(page, field).getByRole("combobox").fill(ONLY_A_OWNER);
      await expect(picker(page, field).getByRole("option")).toHaveCount(1);
    }
  });

  test("a customer id of the other organization in the address filters nothing and reveals nothing", async ({ page }) => {
    await page.goto(horses(ORG_A.id, `?owner_customer_id=${ids.b.onlyOwner}`));

    await expect(page.getByTestId("horse-row")).toHaveCount(0);
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue("Unknown customer");
    expect(await page.locator("main").innerText()).not.toContain(ONLY_B_OWNER);
    expect(await page.content()).not.toContain(ONLY_B_OWNER);
  });
});

test.describe("direct navigation to another organization's horse", () => {
  async function pageOutcome(page: Page, url: string) {
    const response = await page.goto(url);
    await expect(page.getByTestId("not-found")).toBeVisible();
    return { status: response?.status(), text: await page.locator("body").innerText() };
  }

  test("a foreign horse id is the same not-found as a random or a malformed one", async ({ page }) => {
    const random = await pageOutcome(page, `${horses(ORG_A.id)}/${RANDOM_ORG}`);
    const malformed = await pageOutcome(page, `${horses(ORG_A.id)}/not-a-uuid`);
    const foreign = await pageOutcome(page, `${horses(ORG_A.id)}/${ids.b.horse}`);

    expect(foreign).toEqual(random);
    expect(malformed).toEqual(random);
    for (const text of [TWIN_HORSE, ONLY_B_HORSE, TWIN_OWNER, ORG_B.name]) expect(foreign.text).not.toContain(text);
  });

  test("a member of B only sees the same not-found for every Horses page of organization A", async ({ page, context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);

    const reference = await pageOutcome(page, `/o/${RANDOM_ORG}/horses`);
    for (const path of [horses(ORG_A.id), `${horses(ORG_A.id)}/new`, `${horses(ORG_A.id)}/${ids.a.horse}`]) {
      expect(await pageOutcome(page, path)).toEqual(reference);
    }
    await pageOutcome(page, `${horses(ORG_B.id)}/${ids.a.horse}`); // and A's horse under B's address
  });
});

test.describe("edit attempts against foreign ids cannot mutate", () => {
  test("PATCH and DELETE on a foreign horse answer 404 exactly as for a random id, and change nothing", async ({ context }) => {
    const before = testRow(`select name || '|' || owner_customer_id || '|' || active::text from horses where id = ${sql(ids.b.horse)}`);

    for (const [method, data] of [["patch", { name: "HACKED", active: false }], ["patch", { owner_customer_id: ids.a.owner }], ["delete", undefined]] as const) {
      const viaA = await outcome(await context.request[method](bffUrl(ORG_A.id, `/horses/${ids.b.horse}`), { data }));
      const random = await outcome(await context.request[method](bffUrl(ORG_A.id, `/horses/${RANDOM_ORG}`), { data }));
      expect(viaA.status).toBe(404);
      expect(viaA).toEqual(random);
    }
    expect(testRow(`select name || '|' || owner_customer_id || '|' || active::text from horses where id = ${sql(ids.b.horse)}`)).toBe(before);
  });

  test("a customer of the other organization cannot be assigned to this organization's horse: refused like a random id, horse unchanged", async ({ context }) => {
    const before = testRow(`select owner_customer_id || '|' || (stable_customer_id is null)::text from horses where id = ${sql(ids.a.horse)}`);

    for (const field of ["owner_customer_id", "stable_customer_id"]) {
      const viaForeign = await outcome(await context.request.patch(bffUrl(ORG_A.id, `/horses/${ids.a.horse}`), { data: { [field]: ids.b.owner } }));
      const viaRandom = await outcome(await context.request.patch(bffUrl(ORG_A.id, `/horses/${ids.a.horse}`), { data: { [field]: RANDOM_ORG } }));
      expect(viaForeign.status).toBe(422);
      expect(viaForeign).toEqual(viaRandom);
      expect(viaForeign.body).not.toContain(TWIN_OWNER);
    }
    expect(testRow(`select owner_customer_id || '|' || (stable_customer_id is null)::text from horses where id = ${sql(ids.a.horse)}`)).toBe(before);
  });

  test("a member of B only cannot change A's horses through either address", async ({ context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);
    const before = testRow(`select name || '|' || active::text from horses where id = ${sql(ids.a.horse)}`);

    for (const org of [ORG_A, ORG_B]) {
      const response = await context.request.patch(bffUrl(org.id, `/horses/${ids.a.horse}`), { data: { name: "HACKED", active: false } });
      expect(response.status()).toBe(404);
    }
    expect(testRow(`select name || '|' || active::text from horses where id = ${sql(ids.a.horse)}`)).toBe(before);
  });
});

test.describe("switching organizations", () => {
  test("a draft with chosen customers, and an open picker, do not follow the user to the other organization", async ({ page }) => {
    await page.goto(`${horses(ORG_A.id)}/new`);
    await page.getByLabel("Name", { exact: true }).fill("Draft typed in A");
    await pick(page, "owner_customer_id", ONLY_A_OWNER);
    await picker(page, "stable_customer_id").getByRole("combobox").fill(TAG); // left open with results

    await switchTo(page, ORG_B);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Horses" }).click();
    await page.getByTestId("new-horse").click();

    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    await expect(page.getByLabel("Name", { exact: true })).toHaveValue("");
    await expect(picker(page, "owner_customer_id").getByRole("combobox")).toHaveValue("");
    expect(await ownerId(page)).toBe("");
    expect(await page.content()).not.toContain(ONLY_A_OWNER);
  });

  test("a picker answer for organization A that is still on its way cannot appear in organization B", async ({ page }) => {
    await page.route(`**/api/o/${ORG_A.id}/customers**`, async (route) => {
      const response = await route.fetch(); // the request goes out at once...
      await new Promise((resolve) => setTimeout(resolve, 2000)); // ...the answer is slow
      await route.fulfill({ response }).catch(() => {}); // the page may be gone by then
    });
    await page.goto(`${horses(ORG_A.id)}/new`);
    await picker(page, "owner_customer_id").getByRole("combobox").click(); // asks A; no answer yet
    await expect(picker(page, "owner_customer_id").getByText("Searching…")).toBeVisible();

    await switchTo(page, ORG_B);
    await page.goto(`${horses(ORG_B.id)}/new`);
    await picker(page, "owner_customer_id").getByRole("combobox").fill(TAG);
    await expect(picker(page, "owner_customer_id").getByRole("option")).toHaveCount(2);
    await page.waitForTimeout(2500); // long enough for A's answer to have arrived anywhere it could

    const offered = (await choices(page, "owner_customer_id")).join("|");
    expect(offered).toContain(ONLY_B_OWNER);
    expect(offered).not.toContain(ONLY_A_OWNER);
    expect(await page.content()).not.toContain(ONLY_A_OWNER);
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
  });

  test("a save started in A lands in A even if the user switches to B before it is answered", async ({ page }) => {
    const name = unique("In Flight Horse");
    await page.route(`**/api/o/${ORG_A.id}/horses`, async (route) => {
      const response = await route.fetch();
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({ response }).catch(() => {});
    });
    await page.goto(`${horses(ORG_A.id)}/new`);
    await page.getByLabel("Name", { exact: true }).fill(name);
    await pick(page, "owner_customer_id", ONLY_A_OWNER);

    await page.getByTestId("submit").click();
    await switchTo(page, ORG_B);
    await expect(page).toHaveURL(`/o/${ORG_B.id}`);

    await expect.poll(() => testRow(`select count(*) from horses where name = ${sql(name)}`)).toBe("1");
    expect(testRow(`select organization_id || '|' || owner_customer_id from horses where name = ${sql(name)}`)).toBe(`${ORG_A.id}|${ids.a.onlyOwner}`);
    await page.goto(horses(ORG_B.id, `?q=${encodeURIComponent(name)}`));
    await expect(page.getByTestId("empty")).toBeVisible();
  });
});

test.describe("two tabs", () => {
  test("each tab assigns its own organization's customer, even with identical names", async ({ context }) => {
    const nameA = unique("Tab Horse");
    const tabA = await context.newPage();
    const tabB = await context.newPage();
    await tabA.goto(`${horses(ORG_A.id)}/new`);
    await tabB.goto(`${horses(ORG_B.id)}/new`);

    for (const tab of [tabA, tabB]) {
      await tab.getByLabel("Name", { exact: true }).fill(nameA);
      await tab.getByLabel("Name", { exact: true }).blur();
      await picker(tab, "owner_customer_id").getByRole("combobox").fill(TWIN_OWNER);
      await picker(tab, "owner_customer_id").getByRole("option").click();
    }
    await tabB.getByTestId("submit").click();
    await expect(tabB.getByTestId("created")).toBeVisible();
    await tabA.getByTestId("submit").click();
    await expect(tabA.getByTestId("created")).toBeVisible();

    expect(testRow(`select organization_id || '|' || owner_customer_id from horses where name = ${sql(nameA)} order by organization_id`)).toBe(`${ORG_A.id}|${ids.a.owner}\n${ORG_B.id}|${ids.b.owner}`);
  });
});

test.describe("browser history", () => {
  test("back and forward across an organization switch show the organization in the address, with its own horses", async ({ page }) => {
    await page.goto(horses(ORG_A.id, `?q=${encodeURIComponent(TAG)}`));
    await switchTo(page, ORG_B);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Horses" }).click();
    await page.getByRole("search").getByLabel("Search").fill(TAG);
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    expect((await horseNames(page)).sort()).toEqual([ONLY_B_HORSE, TWIN_HORSE]);

    for (let step = 0; step < 3; step += 1) {
      await page.goBack();
      const org = page.url().includes(ORG_A.id) ? ORG_A : ORG_B;
      await expect(page.getByTestId("org-name")).toHaveText(org.name);
      const text = await page.locator("main").innerText();
      for (const name of org === ORG_A ? [ONLY_B_HORSE, ONLY_B_OWNER] : [ONLY_A_HORSE, ONLY_A_OWNER]) expect(text).not.toContain(name);
    }
    expect(page.url()).toContain(`/o/${ORG_A.id}/horses`);
    expect((await horseNames(page)).sort()).toEqual([ONLY_A_HORSE, TWIN_HORSE]);

    for (let step = 0; step < 3; step += 1) await page.goForward();
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    expect((await horseNames(page)).sort()).toEqual([ONLY_B_HORSE, TWIN_HORSE]);
  });

  test("going back to a horse of A after viewing B's twin shows A's owner id, not B's", async ({ page }) => {
    await page.goto(`${horses(ORG_A.id)}/${ids.a.horse}`);
    expect(await ownerId(page)).toBe(ids.a.owner);
    await switchTo(page, ORG_B);
    await page.goto(`${horses(ORG_B.id)}/${ids.b.horse}`);
    expect(await ownerId(page)).toBe(ids.b.owner);

    await page.goBack();
    await page.goBack();

    await expect(page).toHaveURL(`${horses(ORG_A.id)}/${ids.a.horse}`);
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);
    expect(await ownerId(page)).toBe(ids.a.owner);
  });
});

test.describe("forged headers are ineffective on the Horses workflows", () => {
  const FORGED = { "x-organization-id": ORG_B.id, "x-dev-user-email": MARIA, authorization: "Bearer forged", "x-forwarded-for": "10.0.0.1" };

  test("a forged organization header cannot move a read or a write, or smuggle in the other organization's customer", async ({ context }) => {
    const list = await context.request.get(bffUrl(ORG_A.id, `/horses?q=${encodeURIComponent(TAG)}`), { headers: FORGED });
    expect(((await list.json()) as { name: string }[]).map((horse) => horse.name).sort()).toEqual([ONLY_A_HORSE, TWIN_HORSE]);

    const name = unique("Forged Horse");
    const created = await context.request.post(bffUrl(ORG_A.id, "/horses"), { headers: FORGED, data: { name, owner_customer_id: ids.a.owner } });
    expect(created.status()).toBe(201);
    expect(testRow(`select organization_id from horses where name = ${sql(name)}`)).toBe(ORG_A.id);

    // The forged header names B, but the request is A's, so B's customer is foreign to it.
    const smuggled = await context.request.post(bffUrl(ORG_A.id, "/horses"), { headers: FORGED, data: { name: unique("Smuggle"), owner_customer_id: ids.b.owner } });
    expect(smuggled.status()).toBe(422);
  });

  test("a forged identity cannot read or change A's horses for a member of B only", async ({ context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);
    const asFredrik = { ...FORGED, "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id };
    const name = unique("Forged Identity Horse");

    const read = await context.request.get(bffUrl(ORG_A.id, "/horses"), { headers: asFredrik });
    const write = await context.request.post(bffUrl(ORG_A.id, "/horses"), { headers: asFredrik, data: { name, owner_customer_id: ids.a.owner } });
    const edit = await context.request.patch(bffUrl(ORG_A.id, `/horses/${ids.a.horse}`), { headers: asFredrik, data: { name: "HACKED" } });

    expect([read.status(), write.status(), edit.status()]).toEqual([404, 404, 404]);
    expect(testRow(`select count(*) from horses where name = ${sql(name)}`)).toBe("0");
    expect(testRow(`select name from horses where id = ${sql(ids.a.horse)}`)).toBe(TWIN_HORSE);
  });

  test("without the session cookie, forged headers get nothing", async ({ request }) => {
    const headers = { "x-dev-user-email": FREDRIK, "x-organization-id": ORG_A.id };
    expect((await request.get(bffUrl(ORG_A.id, "/horses"), { headers })).status()).toBe(401);
    expect((await request.patch(bffUrl(ORG_A.id, `/horses/${ids.a.horse}`), { headers, data: { name: "HACKED" } })).status()).toBe(401);
  });
});

test("after all of the above, no horse in the database refers to a customer of another organization", async () => {
  expect(testRow(`select count(*) from horses h join customers c on c.id = h.owner_customer_id where c.organization_id <> h.organization_id`)).toBe("0");
  expect(testRow(`select count(*) from horses h join customers c on c.id = h.stable_customer_id where c.organization_id <> h.organization_id`)).toBe("0");
});
