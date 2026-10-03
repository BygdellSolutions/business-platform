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

/** Create a horse through the BFF. */
export async function createHorse(
  context: BrowserContext,
  orgId: string,
  data: { name: string; owner_customer_id: string; stable_customer_id?: string | null; birth_year?: number; sex?: "mare" | "stallion" | "gelding"; breed?: string; active?: boolean },
) {
  const response = await context.request.post(bffUrl(orgId, "/horses"), { data });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; name: string };
}

/** Deactivate or reactivate any record through the BFF. */
export async function setActive(context: BrowserContext, orgId: string, area: "customers" | "items" | "horses", id: string, active: boolean) {
  const response = await context.request.patch(bffUrl(orgId, `/${area}/${id}`), { data: { active } });
  expect(response.status(), await response.text()).toBe(200);
}

/** The entity picker for an API field (the owner is `owner_customer_id`). */
export const picker = (page: Page, field: string) => page.getByTestId(`picker-${field}`);

/** Open a picker, type to narrow it, and choose the option showing `name`. */
export async function pick(page: Page, field: string, name: string): Promise<void> {
  const input = picker(page, field).getByRole("combobox");
  await input.click();
  await input.fill(name);
  await picker(page, field).getByRole("option").filter({ hasText: name }).first().click();
  await expect(input).toHaveValue(name);
}

/** The labels of the options a picker currently lists. */
export async function choices(page: Page, field: string): Promise<string[]> {
  return picker(page, field).getByRole("option").allTextContents();
}

// --- Sales: transactions ----------------------------------------------------------------------------------------------

export interface TxJson {
  id: string;
  status: "draft" | "completed" | "cancelled";
  version: number;
  header_version: number;
  billing_customer_id: string;
  transaction_date: string;
  totals: { net_amount: string; vat_amount: string; gross_amount: string; vat_breakdown: { vat_rate: string; net_amount: string; vat_amount: string }[] };
  lines: { id: string; version: number; description: string; unit: string; quantity: string; unit_price_ex_vat: string; vat_rate: string; net_amount: string; item_id: string | null }[];
}

/** The If-Match header for a version, as the browser sends it. */
export const ifMatch = (version: number) => ({ "if-match": `"${version}"` });

export async function createTransaction(
  context: BrowserContext,
  orgId: string,
  data: { billing_customer_id: string; transaction_date?: string; lines?: Record<string, string>[] },
): Promise<TxJson> {
  const response = await context.request.post(bffUrl(orgId, "/transactions"), { data });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as TxJson;
}

export async function getTransaction(context: BrowserContext, orgId: string, id: string): Promise<TxJson> {
  const response = await context.request.get(bffUrl(orgId, `/transactions/${id}`));
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as TxJson;
}

/** Add a line the way another tab or user would (no version needed to add). */
export async function addLine(context: BrowserContext, orgId: string, txId: string, data: Record<string, string>) {
  const response = await context.request.post(bffUrl(orgId, `/transactions/${txId}/lines`), { data });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string; version: number };
}

/** Complete, reopen or cancel as "someone else": reads the current version, then acts on it. */
export async function lifecycle(context: BrowserContext, orgId: string, id: string, action: "complete" | "reopen" | "cancel"): Promise<TxJson> {
  const current = await getTransaction(context, orgId, id);
  const response = await context.request.post(bffUrl(orgId, `/transactions/${id}/${action}`), { headers: ifMatch(current.version) });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as TxJson;
}

/** Edit a line as "someone else": reads its current version, then patches on it. */
export async function editLine(context: BrowserContext, orgId: string, txId: string, lineId: string, data: Record<string, string>) {
  const current = await getTransaction(context, orgId, txId);
  const version = current.lines.find((line) => line.id === lineId)!.version;
  const response = await context.request.patch(bffUrl(orgId, `/transactions/${txId}/lines/${lineId}`), { data, headers: ifMatch(version) });
  expect(response.status(), await response.text()).toBe(200);
}

/** The browser's own idea of today, as YYYY-MM-DD (what the create form prefills). */
export const browserToday = (page: Page) => page.evaluate(() => new Date().toLocaleDateString("sv-SE"));

/** Run `body` while a REQUIRED custom field on transactions exists, and always disable it afterwards. */
export async function withRequiredTransactionField(context: BrowserContext, orgId: string, body: (label: string) => Promise<void>) {
  const key = `proj_${randomUUID().slice(0, 8).replaceAll("-", "")}`;
  const label = `Project ${key}`;
  const made = await context.request.post(bffUrl(orgId, "/custom-fields/definitions"), { data: { entity_type: "transaction", key, label, field_type: "text", required: true } });
  expect(made.status(), await made.text()).toBe(201);
  const definition = (await made.json()) as { id: string };
  try {
    await body(label);
  } finally {
    const off = await context.request.patch(bffUrl(orgId, `/custom-fields/definitions/${definition.id}`), { data: { enabled: false } });
    expect(off.status(), await off.text()).toBe(200);
  }
}

/**
 * Open the add-line form. The first click after a page load can land before React has attached
 * its handlers (the server-rendered button exists a moment earlier), so it is retried until the
 * form is really open.
 */
export async function openAddLine(page: Page): Promise<void> {
  await expect(async () => {
    if (!(await page.getByTestId("add-line-form").isVisible())) await page.getByTestId("add-line").click();
    await expect(page.getByTestId("add-line-form")).toBeVisible({ timeout: 1500 });
  }).toPass({ timeout: 15_000 });
}
