import { expect, test, type Page } from "@playwright/test";

import { BASE_URL } from "./env";
import {
  FREDRIK,
  MARIA,
  ORG_A,
  ORG_B,
  RANDOM_ORG,
  createCustomer,
  expectOrganization,
  previewNames,
  signIn,
} from "./support";

/**
 * Organization isolation as the BROWSER experiences it. The seed gives both organizations a
 * customer named "Anna Andersson"; these tests add one customer that exists in only one
 * organization each, so any leak of data (or of stale state) across a switch is visible.
 */

const ONLY_A = "Only In Org A";
const ONLY_B = "Only In Org B";

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL });
  await signIn(context, FREDRIK);
  await createCustomer(context, ORG_A.id, ONLY_A);
  await createCustomer(context, ORG_B.id, ONLY_B);
  await context.close();
});

test.beforeEach(async ({ context }) => {
  await signIn(context, FREDRIK);
});

async function expectShowsOnly(page: Page, org: typeof ORG_A | typeof ORG_B) {
  await expectOrganization(page, org);
  const names = await previewNames(page);
  const mine = org === ORG_A ? ONLY_A : ONLY_B;
  const theirs = org === ORG_A ? ONLY_B : ONLY_A;
  expect(names).toContain(mine);
  expect(names).not.toContain(theirs);
  expect(names.join("|")).not.toContain("Org " + (org === ORG_A ? "B" : "A"));
}

test.describe("the organization in the URL grants nothing by itself", () => {
  async function notFoundOutcome(page: Page, url: string) {
    const response = await page.goto(url);
    await expect(page.getByTestId("not-found")).toBeVisible();
    return { status: response?.status(), text: await page.locator("body").innerText() };
  }

  test("an arbitrary organization UUID is a 404 with nothing about any organization", async ({ page }) => {
    const outcome = await notFoundOutcome(page, `/o/${RANDOM_ORG}`);

    expect(outcome.status).toBe(404);
    expect(outcome.text).not.toContain(ORG_A.name);
    expect(outcome.text).not.toContain(ORG_B.name);
  });

  test("an organization that belongs to someone else looks exactly like one that does not exist", async ({ page, context }) => {
    const nonexistent = await notFoundOutcome(page, `/o/${RANDOM_ORG}`);
    const malformed = await notFoundOutcome(page, "/o/not-a-uuid");

    await context.clearCookies();
    await signIn(context, MARIA); // maria belongs to B only, so A is real but not hers
    const foreign = await notFoundOutcome(page, `/o/${ORG_A.id}`);

    expect(foreign.status).toBe(404);
    expect(foreign).toEqual(nonexistent);
    expect(malformed).toEqual(nonexistent);
    expect(foreign.text).not.toContain(ORG_A.name);
  });

  test("a foreign organization's sub-pages are the same 404", async ({ page, context }) => {
    await context.clearCookies();
    await signIn(context, MARIA);

    for (const path of ["", "/customers", "/transactions/anything"]) {
      const response = await page.goto(`/o/${ORG_A.id}${path}`);
      expect(response?.status()).toBe(404);
      await expect(page.getByTestId("not-found")).toBeVisible();
    }
  });

  test("the organization's own member still gets in", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}`);
    await expectShowsOnly(page, ORG_A);
  });
});

test.describe("switching organizations", () => {
  test("shows the other organization's data and none of the previous one's", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}`);
    await expectShowsOnly(page, ORG_A);

    await page.getByTestId("org-switcher").getByRole("link", { name: ORG_B.name }).click();

    await expect(page).toHaveURL(new RegExp(`/o/${ORG_B.id}$`));
    await expectShowsOnly(page, ORG_B);
    await expect(page.getByTestId("org-role")).toHaveText("admin");
  });

  test("is a full page load, not a client-side transition", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}`);
    await expectShowsOnly(page, ORG_A);
    await page.evaluate(() => {
      (window as unknown as { __sameDocument: string }).__sameDocument = "still the old document";
    });

    await page.getByTestId("org-switcher").getByRole("link", { name: ORG_B.name }).click();
    await expectShowsOnly(page, ORG_B);

    expect(await page.evaluate(() => (window as unknown as { __sameDocument?: string }).__sameDocument)).toBeUndefined();
  });

  test("cannot preserve tenant-specific client state", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}`);
    await previewNames(page);
    await page.getByLabel("Filter preview").fill("Anna");
    await expect(page.getByTestId("customer-preview-item")).toHaveText(["Anna Andersson"]);

    await page.getByTestId("org-switcher").getByRole("link", { name: ORG_B.name }).click();
    await expectShowsOnly(page, ORG_B);

    await expect(page.getByLabel("Filter preview")).toHaveValue("");
    expect((await previewNames(page)).length).toBeGreaterThan(1); // unfiltered list of B
  });

  test("never lets a request for the old organization go out after the switch", async ({ page }) => {
    const requests: { org: string; at: "before" | "after" }[] = [];
    let phase: "before" | "after" = "before";
    page.on("request", (request) => {
      const match = new URL(request.url()).pathname.match(/^\/api\/o\/([^/]+)\//);
      if (match) requests.push({ org: match[1], at: phase });
    });

    await page.goto(`/o/${ORG_A.id}`);
    await expectShowsOnly(page, ORG_A);
    phase = "after";
    await page.getByTestId("org-switcher").getByRole("link", { name: ORG_B.name }).click();
    await expectShowsOnly(page, ORG_B);

    expect(requests.filter((r) => r.at === "before").map((r) => r.org)).toEqual([ORG_A.id]);
    expect(requests.filter((r) => r.at === "after").map((r) => r.org)).toEqual([ORG_B.id]);
  });
});

test.describe("tabs", () => {
  test("two tabs stay in different organizations, and each only ever touches its own", async ({ context }) => {
    const tabA = await context.newPage();
    const tabB = await context.newPage();
    await tabA.goto(`/o/${ORG_A.id}`);
    await tabB.goto(`/o/${ORG_B.id}`);
    await expectShowsOnly(tabA, ORG_A);
    await expectShowsOnly(tabB, ORG_B);

    // The cookie is shared by both tabs; the organization is not.
    const created = await tabA.evaluate(async (orgId) => {
      const response = await fetch(`/api/o/${orgId}/customers`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ customer_type: "person", name: "Written From Tab A" }),
      });
      return response.status;
    }, ORG_A.id);
    expect(created).toBe(201);

    await tabA.reload();
    await tabB.reload();
    await expectOrganization(tabA, ORG_A);
    await expectOrganization(tabB, ORG_B);
    expect(await previewNames(tabA)).toContain("Written From Tab A");
    expect(await previewNames(tabB)).not.toContain("Written From Tab A");
    await tabA.close();
    await tabB.close();
  });

  test("switching in one tab does not change what another tab shows or does", async ({ context }) => {
    const first = await context.newPage();
    const second = await context.newPage();
    await first.goto(`/o/${ORG_A.id}`);
    await second.goto(`/o/${ORG_A.id}`);
    await expectShowsOnly(first, ORG_A);
    await expectShowsOnly(second, ORG_A);

    await second.getByTestId("org-switcher").getByRole("link", { name: ORG_B.name }).click();
    await expectShowsOnly(second, ORG_B);

    await first.reload();
    await expectShowsOnly(first, ORG_A); // still organization A: nothing global changed under it
    const status = await first.evaluate(async (orgId) => (await fetch(`/api/o/${orgId}/customers?limit=1`)).status, ORG_A.id);
    expect(status).toBe(200);
    await first.close();
    await second.close();
  });
});

test.describe("browser history", () => {
  test("back and forward never show another tenant's data as current data", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}`);
    await expectShowsOnly(page, ORG_A);
    await page.getByTestId("org-switcher").getByRole("link", { name: ORG_B.name }).click();
    await expect(page).toHaveURL(new RegExp(`/o/${ORG_B.id}$`));
    await expectShowsOnly(page, ORG_B);
    await page.getByTestId("org-switcher").getByRole("link", { name: ORG_A.name }).click();
    await expect(page).toHaveURL(new RegExp(`/o/${ORG_A.id}$`));
    await expectShowsOnly(page, ORG_A);

    const trail: Array<[string, typeof ORG_A | typeof ORG_B]> = [];
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/o/${ORG_B.id}$`));
    trail.push(["back to B", ORG_B]);
    await expectShowsOnly(page, ORG_B);

    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/o/${ORG_A.id}$`));
    trail.push(["back to A", ORG_A]);
    await expectShowsOnly(page, ORG_A);

    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`/o/${ORG_B.id}$`));
    trail.push(["forward to B", ORG_B]);
    await expectShowsOnly(page, ORG_B);

    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`/o/${ORG_A.id}$`));
    trail.push(["forward to A", ORG_A]);
    await expectShowsOnly(page, ORG_A);
    expect(trail).toHaveLength(4);
  });

  test("returning to an organization after another one still shows that organization's own data", async ({ page }) => {
    await page.goto(`/o/${ORG_A.id}`);
    await expectShowsOnly(page, ORG_A);
    await page.goto(`/o/${ORG_B.id}`);
    await expectShowsOnly(page, ORG_B);

    await page.goBack();

    await expect(page).toHaveURL(new RegExp(`/o/${ORG_A.id}$`));
    await expectShowsOnly(page, ORG_A);
  });
});
