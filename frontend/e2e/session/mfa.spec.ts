import type { Browser, Page } from "@playwright/test";

import { expect, test } from "../fixtures";

import { E2E_PASSWORD, signInThroughPage } from "../auth-support";
import { BASE_URL } from "../env";
import { createWorld, type World } from "../support";
import { totp, untilNextStep } from "../totp";

/**
 * The second factor in real browsers: turning it on from the account page (QR code, key, confirming code, recovery codes shown
 * once), then signing in with the password PLUS a recovery code or a fresh code from the "authenticator".
 */

let world: World | undefined;
test.afterEach(() => {
  world?.cleanup();
  world = undefined;
});

async function enrol(page: Page): Promise<{ secret: string; recoveryCodes: string[] }> {
  await page.goto("/account");
  await page.getByTestId("mfa-set-up").click();
  await page.getByTestId("mfa-password").fill(E2E_PASSWORD);
  await page.getByTestId("mfa-password-submit").click();
  await expect(page.getByTestId("mfa-qr")).toBeVisible(); // the QR code was really drawn in the browser
  const secret = (await page.getByTestId("mfa-secret").textContent())!.trim();
  await page.getByTestId("mfa-code").fill(totp(secret));
  await page.getByTestId("mfa-confirm").click();
  await expect(page.getByTestId("mfa-recovery-code")).toHaveCount(10);
  const recoveryCodes = (await page.getByTestId("mfa-recovery-code").allTextContents()).map((code) => code.trim());
  await page.getByTestId("mfa-saved").click();
  await expect(page.getByTestId("mfa-state")).toHaveText("On. 10 recovery codes left.");
  return { secret, recoveryCodes };
}

async function secondBrowser(browser: Browser) {
  const context = await browser.newContext({ baseURL: BASE_URL });
  return { context, page: await context.newPage() };
}

async function passwordStep(page: Page, email: string) {
  await page.goto("/login");
  await expect(page.getByTestId("login-form")).toHaveAttribute("data-ready", "true");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(E2E_PASSWORD);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("mfa-form")).toBeVisible();
}

test("turning it on shows the recovery codes once; the password alone then opens nothing, and a recovery code works exactly once", async ({ page, browser }) => {
  world = createWorld({ label: "MfaRecovery" });
  await signInThroughPage(page, world.email);
  const { recoveryCodes } = await enrol(page);
  await page.reload();
  await expect(page.getByTestId("mfa-recovery-code")).toHaveCount(0); // never shown again

  const { context, page: other } = await secondBrowser(browser);
  await passwordStep(other, world.email);
  await expect(other).toHaveURL(/\/login/); // the password alone did not sign anyone in
  await other.getByLabel("Code").fill(recoveryCodes[0]);
  await other.getByTestId("mfa-submit").click();
  await other.waitForURL((url) => !url.pathname.startsWith("/login"));
  await other.goto("/account");
  await expect(other.getByTestId("mfa-state")).toHaveText("On. 9 recovery codes left.");
  await context.close();

  const { context: third, page: again } = await secondBrowser(browser);
  await passwordStep(again, world.email);
  await again.getByLabel("Code").fill(recoveryCodes[0]); // the same code a second time
  await again.getByTestId("mfa-submit").click();
  await expect(again.getByTestId("mfa-error")).toBeVisible();
  await expect(again).toHaveURL(/\/login/);
  await third.close();
});

test("a fresh code from the authenticator signs in, a wrong one does not, and Start over returns to the password", async ({ page, browser }) => {
  world = createWorld({ label: "MfaTotp" });
  await signInThroughPage(page, world.email);
  const { secret } = await enrol(page);

  const { context, page: other } = await secondBrowser(browser);
  await passwordStep(other, world.email);
  await other.getByLabel("Code").fill("000000");
  await other.getByTestId("mfa-submit").click();
  await expect(other.getByTestId("mfa-error")).toBeVisible();

  await other.getByTestId("mfa-start-over").click();
  await expect(other.getByTestId("login-form")).toBeVisible();
  await passwordStep(other, world.email);

  await page.waitForTimeout(untilNextStep()); // the confirming code is spent: a sign-in needs the next time step
  await other.getByLabel("Code").fill(totp(secret));
  await other.getByTestId("mfa-submit").click();
  await other.waitForURL((url) => !url.pathname.startsWith("/login"));
  await other.goto("/account");
  await expect(other.getByTestId("mfa-state")).toBeVisible();
  await context.close();
});

test("it can be turned off with a current code, after which the password alone signs in again", async ({ page, browser }) => {
  world = createWorld({ label: "MfaOff" });
  await signInThroughPage(page, world.email);
  const { recoveryCodes } = await enrol(page);

  await page.getByTestId("mfa-turn-off").click();
  await page.getByTestId("mfa-current-code").fill(recoveryCodes[1]); // a recovery code is a valid current code
  await page.getByTestId("mfa-current-submit").click();
  await expect(page.getByTestId("mfa-done")).toHaveText("The second factor is off.");
  await expect(page.getByTestId("mfa-set-up")).toBeVisible();

  const { context, page: other } = await secondBrowser(browser);
  await signInThroughPage(other, world.email); // no code step any more
  await expect(other).not.toHaveURL(/\/login/);
  await context.close();
});
