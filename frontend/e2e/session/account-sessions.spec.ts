import { expect, test } from "../fixtures";

import { signInThroughPage } from "../auth-support";
import { BASE_URL } from "../env";
import { createWorld, type World } from "../support";

/**
 * "Where you are signed in" on the account page, with two real browsers of the same person: the list shows both, ending one
 * from the other really signs it out, and "sign out everywhere" signs out this one too.
 */

/**
 * Provisioning a password opens a session of its own (setting a password signs the person in), so the person starts with one
 * session nobody holds. End it first: from here on every session on the page is a browser this test opened.
 */
async function startClean(page: import("@playwright/test").Page) {
  await page.goto("/account");
  if ((await page.getByTestId("session-row").count()) > 1) {
    await page.getByTestId("end-others").click();
    await expect(page.getByTestId("sessions-done")).toBeVisible();
  }
  await expect(page.getByTestId("session-row")).toHaveCount(1);
}

async function secondBrowser(browser: import("@playwright/test").Browser, email: string) {
  const context = await browser.newContext({ baseURL: BASE_URL });
  const other = await context.newPage();
  await signInThroughPage(other, email);
  return { context, other };
}

let world: World | undefined;
test.afterEach(() => {
  world?.cleanup();
  world = undefined;
});

test("the list shows both browsers and ending the other one signs it out for real", async ({ page, browser }) => {
  world = createWorld({ label: "AccountSessions" });
  await signInThroughPage(page, world.email);
  await startClean(page);
  const { context: otherContext, other } = await secondBrowser(browser, world.email);

  await page.goto("/account");
  const rows = page.getByTestId("session-row");
  await expect(rows).toHaveCount(2);
  await expect(page.locator('[data-testid="session-row"][data-current="true"]')).toHaveCount(1);

  await page.getByTestId("end-session").click();
  await expect(page.getByTestId("sessions-done")).toHaveText("That session was signed out.");
  await expect(rows).toHaveCount(1);

  await other.goto("/");
  await expect(other).toHaveURL(/\/login/); // the other browser is signed out on the server, not just hidden
  await page.reload();
  await expect(page.getByTestId("sessions-table")).toBeVisible(); // this one is still signed in
  await otherContext.close();
});

test("signing out everywhere ends this session too and returns to the sign-in page", async ({ page, browser }) => {
  world = createWorld({ label: "AccountSessionsAll" });
  await signInThroughPage(page, world.email);
  await startClean(page);
  const { context: otherContext, other } = await secondBrowser(browser, world.email);

  await page.goto("/account");
  await expect(page.getByTestId("session-row")).toHaveCount(2);
  await page.getByTestId("end-all").click();

  await expect(page).toHaveURL(/\/login/);
  await page.goto("/account");
  await expect(page).toHaveURL(/\/login/);
  await other.goto("/");
  await expect(other).toHaveURL(/\/login/);
  await otherContext.close();
});

test("a person with one other browser can sign out all the others in one step", async ({ page, browser }) => {
  world = createWorld({ label: "AccountSessionsOthers" });
  await signInThroughPage(page, world.email);
  await startClean(page);
  const { context: otherContext, other } = await secondBrowser(browser, world.email);

  await page.goto("/account");
  await page.getByTestId("end-others").click();

  await expect(page.getByTestId("sessions-done")).toHaveText("Every other session was signed out.");
  await expect(page.getByTestId("session-row")).toHaveCount(1);
  await expect(page.getByTestId("end-others")).toBeDisabled();
  await other.goto("/");
  await expect(other).toHaveURL(/\/login/);
  await otherContext.close();
});
