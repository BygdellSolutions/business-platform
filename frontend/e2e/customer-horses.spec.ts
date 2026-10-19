import type { BrowserContext } from "@playwright/test";

import { expect, test } from "./fixtures";
import { bffUrl, createCustomer, createHorse, createWorld, signIn, type World } from "./support";

/**
 * A customer's page shows the horses they own and the horses that stand at their stable, and its information can be folded away.
 */

let world: World;
let other: World;
test.afterEach(() => {
  world?.cleanup();
  other?.cleanup();
});

async function company(context: BrowserContext, orgId: string, name: string) {
  const response = await context.request.post(bffUrl(orgId, "/customers"), { data: { customer_type: "company", name } });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string };
}

test("a customer's page lists the horses they own and the horses at their stable, each once and with its role", async ({ page, context }) => {
  world = createWorld({ label: "Customer horses" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");
  const stable = await company(context, world.orgId, "Umeå HK");
  await createHorse(context, world.orgId, { name: "Kalle", owner_customer_id: anna.id, stable_customer_id: stable.id });
  await createHorse(context, world.orgId, { name: "Storm", owner_customer_id: anna.id });
  await createHorse(context, world.orgId, { name: "Bella", owner_customer_id: stable.id, stable_customer_id: stable.id }); // a stable's own horse standing at its own stable

  await page.goto(`/o/${world.orgId}/customers/${anna.id}`);
  await page.getByTestId("open-horses").click();
  await expect(page.getByTestId("open-horses")).toContainText("Horses (2)");
  await expect(page.getByTestId("customer-horse-link")).toHaveText(["Kalle", "Storm"]);
  await expect(page.getByTestId("customer-horse-role")).toHaveText(["Owner", "Owner"]);

  await page.goto(`/o/${world.orgId}/customers/${stable.id}`);
  await page.getByTestId("open-horses").click();
  await expect(page.getByTestId("customer-horse-link")).toHaveText(["Bella", "Kalle"]);
  await expect(page.getByTestId("customer-horse-role")).toHaveText(["Owner and stable", "Stable"]);

  await page.getByTestId("customer-horse-link").first().click();
  await expect(page.getByTestId("record-name")).toHaveText("Bella");
});

test("New horse on a customer's page starts a horse with that customer as the owner", async ({ page, context }) => {
  world = createWorld({ label: "Customer new horse" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");

  await page.goto(`/o/${world.orgId}/customers/${anna.id}`);
  await page.getByTestId("open-horses").click();
  await expect(page.getByTestId("no-horses")).toBeVisible();
  await page.getByTestId("new-customer-horse").click();
  await expect(page.getByTestId("picker-owner_customer_id").getByRole("combobox")).toHaveValue("Anna Andersson");
  await page.getByLabel("Name").fill("Kalle");
  await page.getByTestId("submit").click();
  await expect(page.getByTestId("record-name")).toHaveText("Kalle");

  await page.goto(`/o/${world.orgId}/customers/${anna.id}`);
  await page.getByTestId("open-horses").click();
  await expect(page.getByTestId("customer-horse-link")).toHaveText(["Kalle"]);
});

test("a made-up or foreign owner in the new-horse address leaves the owner empty", async ({ page, context }) => {
  world = createWorld({ label: "Prefill own" });
  other = createWorld({ label: "Prefill other" });
  await signIn(context, other.email);
  const foreign = await createCustomer(context, other.orgId, "Foreign Anna");
  await signIn(context, world.email);

  for (const id of [foreign.id, "00000000-0000-4000-8000-00000000dead", "not-an-id"]) {
    await page.goto(`/o/${world.orgId}/horses/new?owner_customer_id=${id}`);
    await expect(page.getByTestId("picker-owner_customer_id").getByRole("combobox")).toHaveValue("");
    await expect(page.locator("body")).not.toContainText("Foreign Anna");
  }
});

test("another organization's horses never appear on a customer's page", async ({ page, context }) => {
  world = createWorld({ label: "Isolation horses A" });
  other = createWorld({ label: "Isolation horses B" });
  await signIn(context, other.email);
  const theirAnna = await createCustomer(context, other.orgId, "Anna Andersson");
  await createHorse(context, other.orgId, { name: "Secret Horse", owner_customer_id: theirAnna.id });
  await signIn(context, world.email);
  const myAnna = await createCustomer(context, world.orgId, "Anna Andersson"); // the same name in both organizations

  await page.goto(`/o/${world.orgId}/customers/${myAnna.id}`);
  await page.getByTestId("open-horses").click();
  await expect(page.getByTestId("no-horses")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Secret Horse");
});

test("the customer information is open at first and can be folded away; a viewer reads it and cannot add a horse", async ({ page, context }) => {
  world = createWorld({ label: "Customer info" });
  await signIn(context, world.email);
  const anna = await createCustomer(context, world.orgId, "Anna Andersson");

  await page.goto(`/o/${world.orgId}/customers/${anna.id}`);
  await expect(page.getByTestId("customer-info").getByRole("heading", { name: "Customer information" })).toBeVisible(); // always shown, not collapsible
  await expect(page.getByTestId("info-toggle")).toHaveCount(0);
  await expect(page.getByLabel("Name")).toBeVisible();

  await signIn(context, world.addMember("viewer"));
  await page.goto(`/o/${world.orgId}/customers/${anna.id}`);
  await expect(page.getByTestId("customer-info")).toContainText("Anna Andersson");
  await page.getByTestId("open-horses").click();
  await expect(page.getByTestId("new-customer-horse")).toHaveCount(0);
});
