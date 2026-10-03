import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { expect, type BrowserContext, type Page } from "@playwright/test";

import { POSTGRES_DB, POSTGRES_TEST_DB, POSTGRES_USER, ROOT_DIR } from "./env";

// Seeded by backend/app/scripts/seed_dev.py (fixed ids).
export const ORG_A = { id: "00000000-0000-4000-8000-0000000000a1", name: "Fredrik Horse Therapy" };
export const ORG_B = { id: "00000000-0000-4000-8000-0000000000b2", name: "Umeå Stable Services" };
export const FREDRIK = "fredrik@dev.test"; // owner of A, admin of B
export const MARIA = "maria@dev.test"; // employee of B only
export const RANDOM_ORG = "00000000-0000-4000-8000-00000000dead";

export const bffUrl = (orgId: string, path: string) => `/api/o/${orgId}${path}`;

/** Sign in through the real route handler; the cookie lands in the browser context. */
export async function signIn(context: BrowserContext, email: string): Promise<void> {
  const response = await context.request.post("/api/dev-session", { form: { email }, maxRedirects: 0 });
  expect(response.status()).toBe(303);
}

export async function signInViaUi(page: Page, email: string): Promise<void> {
  await page.goto("/dev-login");
  await page.getByTestId(`login-as-${email}`).click();
}

/** Create a customer through the BFF (as the context's signed-in user) in the given organization. */
export async function createCustomer(context: BrowserContext, orgId: string, name: string) {
  const response = await context.request.post(bffUrl(orgId, "/customers"), {
    data: { customer_type: "person", name },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; name: string };
}

/** The names the dashboard preview shows right now. */
export async function previewNames(page: Page): Promise<string[]> {
  await expect(page.getByTestId("customer-preview")).toBeVisible();
  return page.getByTestId("customer-preview-item").allTextContents();
}

export async function expectOrganization(page: Page, org: { name: string }): Promise<void> {
  await expect(page.getByTestId("org-name")).toHaveText(org.name);
  await expect(page.getByTestId("dashboard-org")).toHaveText(org.name);
}

/** Run SQL in one of the Postgres containers (service `postgres` = dev, `postgres-test` = test). */
export function psql(service: "postgres" | "postgres-test", sql: string): string {
  const database = service === "postgres" ? POSTGRES_DB : POSTGRES_TEST_DB;
  return execFileSync("docker", ["compose", "exec", "-T", service, "psql", "-U", POSTGRES_USER, "-d", database, "-At", "-c", sql], {
    cwd: ROOT_DIR,
    encoding: "utf8",
  }).trim();
}

/** A name nobody else uses, so specs can share one seeded database without interfering. */
export const unique = (prefix: string) => `${prefix} ${randomUUID().slice(0, 8)}`;

/** Create an item through the BFF; money and VAT are decimal STRINGS, as always. */
export async function createItem(
  context: BrowserContext,
  orgId: string,
  data: { name: string; type?: "service" | "product"; unit?: string; price_ex_vat?: string; vat_rate?: string; description?: string; active?: boolean },
) {
  const response = await context.request.post(bffUrl(orgId, "/items"), {
    data: { type: "service", unit: "hour", price_ex_vat: "10.00", vat_rate: "25", ...data },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; name: string; price_ex_vat: string; vat_rate: string };
}

/** Single-quote a value for SQL (the test database only; values here are test names). */
export const sql = (value: string) => `'${value.replaceAll("'", "''")}'`;

/** One row of the TEST database as text, columns joined with "|". */
export function testRow(query: string): string {
  return psql("postgres-test", query);
}
