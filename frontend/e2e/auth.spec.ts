import { expect, test } from "./fixtures";

import { FREDRIK, MARIA, ORG_A, ORG_B, signIn, signInViaUi } from "./support";

test.describe("development sign-in", () => {
  test("everything redirects to /dev-login without an identity", async ({ page }) => {
    for (const url of ["/", `/o/${ORG_A.id}`, `/o/${ORG_B.id}`]) {
      await page.goto(url);
      await expect(page).toHaveURL(/\/dev-login$/);
    }
    await expect(page.getByRole("heading", { name: "Development sign-in" })).toBeVisible();
  });

  test("a user the backend does not know is refused, and nothing is stored", async ({ page, context }) => {
    await page.goto("/dev-login");

    await page.getByLabel("Email").fill("nobody@dev.test");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await expect(page.getByTestId("login-error")).toContainText("does not know an active user");
    expect((await context.cookies()).find((c) => c.name === "bp_dev_user")).toBeUndefined();
    await page.goto("/");
    await expect(page).toHaveURL(/\/dev-login$/);
  });

  test("a user with several organizations chooses one and sees it in the shell", async ({ page }) => {
    await signInViaUi(page, FREDRIK);

    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
    await expect(page.getByTestId("organization-list").getByRole("link")).toHaveText([ORG_A.name, ORG_B.name]);

    await page.getByRole("link", { name: ORG_A.name }).click();

    await expect(page).toHaveURL(new RegExp(`/o/${ORG_A.id}$`));
    await expect(page.getByTestId("org-name")).toHaveText(ORG_A.name);
    await expect(page.getByTestId("org-role")).toHaveText("owner");
    await expect(page.getByTestId("user-email")).toHaveText(FREDRIK);
    await expect(page.getByTestId("dashboard-org")).toHaveText(ORG_A.name);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("a user with one organization goes straight to it, with the role they hold there", async ({ page }) => {
    await signInViaUi(page, MARIA);

    await expect(page).toHaveURL(new RegExp(`/o/${ORG_B.id}$`));
    await expect(page.getByTestId("org-name")).toHaveText(ORG_B.name);
    await expect(page.getByTestId("org-role")).toHaveText("employee");
  });

  test("the same user holds a different role in each organization", async ({ page }) => {
    await signInViaUi(page, FREDRIK);

    await page.goto(`/o/${ORG_A.id}`);
    await expect(page.getByTestId("org-role")).toHaveText("owner");
    await page.goto(`/o/${ORG_B.id}`);
    await expect(page.getByTestId("org-role")).toHaveText("admin");
  });

  test("the identity cookie cannot be read by page scripts", async ({ page, context }) => {
    await signInViaUi(page, FREDRIK);
    await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();

    const cookie = (await context.cookies()).find((c) => c.name === "bp_dev_user");
    expect(cookie).toMatchObject({ httpOnly: true, sameSite: "Lax" });
    expect(decodeURIComponent(cookie!.value)).toBe(FREDRIK); // cookie values are URL-encoded on the wire
    expect(await page.evaluate(() => document.cookie)).not.toContain("bp_dev_user");
  });

  test("signing out clears the identity", async ({ page }) => {
    await signInViaUi(page, FREDRIK);
    await page.goto(`/o/${ORG_A.id}`);

    await page.getByRole("button", { name: "Sign out" }).click();

    await expect(page).toHaveURL(/\/dev-login$/);
    await page.goto(`/o/${ORG_A.id}`);
    await expect(page).toHaveURL(/\/dev-login$/);
  });

  test("a cookie naming an unknown user is no identity at all", async ({ page, context }) => {
    await context.addCookies([{ name: "bp_dev_user", value: "ghost@dev.test", url: "http://127.0.0.1:3100" }]);

    await page.goto(`/o/${ORG_A.id}`);

    await expect(page).toHaveURL(/\/dev-login$/);
  });

  test("a tab that loses its identity is sent to sign in when it next calls the backend", async ({ page, context }) => {
    await signIn(context, FREDRIK);
    await page.goto(`/o/${ORG_A.id}`);
    await expect(page.getByTestId("customer-preview")).toBeVisible();

    await context.clearCookies();
    await page.reload();

    await expect(page).toHaveURL(/\/dev-login$/);
  });
});
