import { expect, test } from "./fixtures";

import { createWorld, signIn, sql, testRow, psql, type World } from "./support";

/**
 * The support page: open to anyone, a message box that makes a ticket (kept before any email is tried), an email link as the other
 * way in, and (signed in) the account behind the message.
 */

let world: World;
const tags: string[] = [];
test.afterEach(() => {
  world?.cleanup();
  for (const tag of tags.splice(0)) psql("postgres-test", `delete from support_tickets where email_lower like ${sql(`%${tag}%`)}`);
});

const unique = () => {
  const tag = Math.random().toString(36).slice(2, 8);
  tags.push(tag);
  return tag;
};

test("anyone can open the support page; the email link is still there as the other way in", async ({ page }) => {
  await page.goto("/support");
  await expect(page.getByRole("heading", { level: 1, name: "Support" })).toBeVisible();
  const href = await page.getByTestId("email-support").getAttribute("href");
  expect(href).toMatch(/^mailto:support@bygdell\.solutions\?subject=Support%20request&body=/);
  expect(decodeURIComponent(href!)).not.toContain("Account:"); // not signed in: nothing about an account
  await expect(page.getByTestId("support-address")).toHaveText("support@bygdell.solutions");
  await expect(page.getByTestId("support-include")).toContainText("what happened instead");
  await expect(page.getByTestId("support-form")).toBeVisible();
});

test("someone who is not signed in sends a message and gets a ticket number; the message is kept", async ({ page }) => {
  const tag = unique();
  await page.goto("/support");
  await expect(page.getByTestId("support-send")).toBeDisabled(); // nothing written yet

  await page.getByLabel("Your name (optional)").fill("Anna Andersson");
  await page.getByLabel("Your email").fill(`anna-${tag}@example.test`);
  await page.getByLabel("How can we help?").fill(`I cannot find where to change a price. ${tag}`);
  await expect(page.getByTestId("support-count")).toContainText("/ 4000");
  await page.getByTestId("support-send").click();

  await expect(page.getByTestId("support-sent")).toBeVisible();
  const reference = (await page.getByTestId("support-reference").textContent())!.trim();
  expect(reference).toMatch(/^SUP-[2-9A-HJKMNP-Z]{8}$/);
  await expect(page.getByTestId("support-sent")).toContainText(`anna-${tag}@example.test`);

  // The ticket is in the database whatever happened to the email (here no provider is configured, so it says so).
  expect(testRow(`select reference || '|' || email || '|' || (user_id is null) || '|' || status from support_tickets where reference = ${sql(reference)}`)).toBe(
    `${reference}|anna-${tag}@example.test|true|open`,
  );
  expect(testRow(`select email_status from support_tickets where reference = ${sql(reference)}`)).toBe("not_configured");

  await page.getByTestId("support-another").click();
  await expect(page.getByTestId("support-form")).toBeVisible();
  await expect(page.getByLabel("How can we help?")).toHaveValue(""); // the box is empty again, the address is kept
});

test("a bad address or a too short message is explained, and nothing is kept", async ({ page }) => {
  const tag = unique();
  await page.goto("/support");
  await page.getByLabel("Your email").fill(`not an address ${tag}`);
  await page.getByLabel("How can we help?").fill("short");
  await page.getByTestId("support-send").click();
  await expect(page.getByText("Enter an email address we can answer")).toBeVisible();
  await expect(page.getByTestId("support-sent")).toHaveCount(0);
  expect(testRow(`select count(*) from support_tickets where email_lower like ${sql(`%${tag}%`)}`)).toBe("0");
});

test("signed in, the menu leads to it, the account is the sender and the ticket belongs to it", async ({ page, context }) => {
  world = createWorld({ label: "Support" });
  await signIn(context, world.email);
  await page.goto(`/o/${world.orgId}`);
  await page.getByTestId("user-menu-toggle").click();
  await page.getByTestId("support-link").click();
  await expect(page).toHaveURL(/\/support$/);

  await expect(page.getByTestId("support-signed-in")).toContainText(world.email);
  await expect(page.getByLabel("Your email")).toHaveCount(0); // no address to type: it is the account's
  const href = decodeURIComponent((await page.getByTestId("email-support").getAttribute("href"))!);
  expect(href).toContain(`Account: ${world.email}`);

  await page.getByLabel("How can we help?").fill("The invoice PDF shows the wrong date for me.");
  await page.getByTestId("support-send").click();
  const reference = (await page.getByTestId("support-reference").textContent())!.trim();
  expect(testRow(`select email || '|' || (user_id is not null) from support_tickets where reference = ${sql(reference)}`)).toBe(`${world.email}|true`);
  psql("postgres-test", `delete from support_tickets where reference = ${sql(reference)}`);
});

test("a program that fills the hidden field is told it worked and nothing is kept; another site cannot post here", async ({ page, context }) => {
  const tag = unique();
  await page.goto("/support");
  await expect(page.getByTestId("support-trap")).not.toBeInViewport(); // off screen: a person never sees it

  const forged = await context.request.post("/api/support", {
    data: { name: "Bot", email: `bot-${tag}@example.test`, message: "Buy cheap watches now please", website: "http://spam.example" },
  });
  expect(forged.status()).toBe(201);
  expect(testRow(`select count(*) from support_tickets where email_lower like ${sql(`%${tag}%`)}`)).toBe("0");

  const foreign = await context.request.post("/api/support", {
    data: { email: `evil-${tag}@example.test`, message: "Posted from another site entirely" },
    headers: { origin: "https://evil.example" },
  });
  expect(foreign.status()).toBe(403);
  expect(testRow(`select count(*) from support_tickets where email_lower like ${sql(`%${tag}%`)}`)).toBe("0");
});

test("the homepage leads to it", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("footer-support").click();
  await expect(page).toHaveURL(/\/support$/);
});
