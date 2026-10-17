import { expect, test } from "./fixtures";

import { createCustomer, createHorse, createWorld, signIn, type World } from "./support";

/**
 * The top bar: a quick search in the middle, the organization drop-down to the left of the avatar, and the avatar (a round bubble
 * with the person's initials) in the top right, which opens the account menu (account, sign out).
 */

let world: World;
test.afterEach(() => world?.cleanup());

test("the avatar bubble opens the account menu, and the organization is a drop-down to the left of it", async ({ page, context }) => {
  world = createWorld({ label: "TopBar" });
  await signIn(context, world.email);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/o/${world.orgId}`);

  const bubble = page.getByTestId("user-menu-toggle");
  const org = page.getByTestId("org-switcher-toggle");
  const search = page.getByTestId("quick-search-input");
  await expect(bubble).toBeVisible();
  expect(await bubble.evaluate((element) => getComputedStyle(element).borderRadius)).toMatch(/50%|9999px|\d{2,}px/); // a bubble

  // top right: the avatar; to the left of it the organization; in the middle the search
  const [b, o, s, view] = [await bubble.boundingBox(), await org.boundingBox(), await search.boundingBox(), page.viewportSize()!];
  expect(b!.x + b!.width).toBeGreaterThan(view.width - 40);
  expect(o!.x + o!.width).toBeLessThanOrEqual(b!.x);
  expect(Math.abs(s!.x + s!.width / 2 - view.width / 2)).toBeLessThan(view.width * 0.2 + 120); // roughly the middle of the page (the sidebar takes the left)
  expect(s!.x + s!.width).toBeLessThanOrEqual(o!.x);

  // the avatar menu: who, account, sign out
  await expect(page.getByRole("menu", { name: "Account" })).toBeHidden();
  await bubble.click();
  await expect(page.getByTestId("user-email")).toBeVisible();
  await expect(page.getByTestId("account-link")).toBeVisible();
  await expect(page.getByTestId("sign-out")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("sign-out")).toBeHidden();
  await bubble.click();
  await page.getByTestId("account-link").click();
  await expect(page).toHaveURL(/\/account/);
});

test("the organization drop-down names where you are and lists your organizations", async ({ page, context }) => {
  world = createWorld({ label: "TopBarOrg" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("org-switcher-current")).toHaveText(world.name);
  await expect(page.getByRole("menu", { name: "Organizations" })).toHaveCount(0);
  await page.getByTestId("org-switcher-toggle").click();
  await expect(page.getByRole("menuitem", { name: new RegExp(world.name) })).toHaveAttribute("aria-current", "true");
  await page.getByTestId("quick-search-input").click(); // a click elsewhere closes it
  await expect(page.getByRole("menu", { name: "Organizations" })).toHaveCount(0);
});

test("the search finds a horse and a page from the top of any page, and a slash starts it", async ({ page, context }) => {
  world = createWorld({ label: "TopBarSearch" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const horse = await createHorse(context, world.orgId, { name: "Stormvind", owner_customer_id: anna.id });
  await page.goto(`/o/${world.orgId}/catalog`);

  await page.keyboard.press("/"); // from anywhere on the page
  await expect(page.getByTestId("quick-search-input")).toBeFocused();
  await page.keyboard.type("storm");
  const hit = page.getByTestId("quick-search-hit").filter({ hasText: "Stormvind" });
  await expect(hit).toHaveAttribute("href", `/o/${world.orgId}/horses/${horse.id}`);
  await page.keyboard.press("Enter"); // the first result
  await expect(page).toHaveURL(new RegExp(`/horses/${horse.id}$`));
  await expect(page.getByTestId("record-name")).toHaveText("Stormvind");

  await page.getByTestId("quick-search-input").fill("anna");
  await page.getByTestId("quick-search-hit").filter({ hasText: "Anna Andersson" }).click();
  await expect(page).toHaveURL(new RegExp(`/customers/${anna.id}$`));

  await page.getByTestId("quick-search-input").fill("invo"); // a page of the menu
  await page.getByRole("option", { name: "Invoices" }).click();
  await expect(page).toHaveURL(new RegExp(`/o/${world.orgId}/invoices$`));
});

test("the search finds nothing of another organization", async ({ page, context }) => {
  world = createWorld({ label: "TopBarIsolation" });
  const other = createWorld({ label: "TopBarOther" });
  try {
    await signIn(context, other.email);
    const secret = await createCustomer(context, other.orgId, "Secretive Stable");
    expect(secret.id).toBeTruthy();
    await signIn(context, world.email);
    await page.goto(`/o/${world.orgId}`);
    await page.getByTestId("quick-search-input").fill("secretive");
    await expect(page.getByTestId("quick-search-empty")).toHaveText("Nothing found.");
  } finally {
    other.cleanup();
  }
});

test("on a phone the top bar fits and nothing scrolls sideways", async ({ page, context }) => {
  world = createWorld({ label: "TopBarPhone" });
  await signIn(context, world.email);
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto(`/o/${world.orgId}`);
  await expect(page.getByTestId("user-menu-toggle")).toBeVisible();
  await expect(page.getByTestId("org-switcher-toggle")).toBeVisible();
  await expect(page.getByTestId("quick-search-input")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  const [b, view] = [await page.getByTestId("user-menu-toggle").boundingBox(), page.viewportSize()!];
  expect(b!.x + b!.width).toBeLessThanOrEqual(view.width);
});
