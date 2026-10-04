import { expect, test } from "../fixtures";

import { FREDRIK, ORG_A, ORG_B } from "../support";
import { signInThroughPage } from "../auth-support";

test("an unauthenticated browser is sent to /login and a real login reaches the organizations", async ({ page, context }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  await signInThroughPage(page, FREDRIK);

  await expect(page.getByRole("heading", { name: "Choose an organization" })).toBeVisible();
  await expect(page.getByTestId("user-email")).toHaveText(FREDRIK);
  await page.getByRole("link", { name: ORG_A.name }).click();
  await expect(page.getByTestId("org-role")).toHaveText("owner");
  await page.goto(`/o/${ORG_B.id}`);
  await expect(page.getByTestId("org-role")).toHaveText("admin");
  const names = (await context.cookies()).map((c) => c.name).sort();
  expect(names).toEqual(["bp_csrf", "bp_session"]);
});
