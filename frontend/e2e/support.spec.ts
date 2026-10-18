import { expect, test } from "./fixtures";

import { createWorld, signIn, type World } from "./support";

/** The support page: reachable without signing in, an email link to the support address, and (signed in) the account in the email. */

let world: World;
test.afterEach(() => world?.cleanup());

test("anyone can open the support page and write to the support address", async ({ page }) => {
  await page.goto("/support");
  await expect(page.getByRole("heading", { level: 1, name: "Support" })).toBeVisible();
  const href = await page.getByTestId("email-support").getAttribute("href");
  expect(href).toMatch(/^mailto:support@bygdell\.solutions\?subject=Support%20request&body=/);
  expect(decodeURIComponent(href!)).not.toContain("Account:"); // not signed in: nothing about an account
  await expect(page.getByTestId("support-address")).toHaveText("support@bygdell.solutions");
  await expect(page.getByTestId("support-include")).toContainText("what happened instead");
  await expect(page.getByTestId("support-account")).toHaveCount(0);
});

test("the homepage leads to it", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("footer-support").click();
  await expect(page).toHaveURL(/\/support$/);
});

test("signed in, the menu leads to it and the email names the account", async ({ page, context }) => {
  world = createWorld({ label: "Support" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}`);
  await page.getByTestId("user-menu-toggle").click();
  await page.getByTestId("support-link").click();
  await expect(page).toHaveURL(/\/support$/);
  await expect(page.getByTestId("support-account")).toContainText(world.email);
  const href = decodeURIComponent((await page.getByTestId("email-support").getAttribute("href"))!);
  expect(href).toContain(`Account: ${world.email}`);
});
