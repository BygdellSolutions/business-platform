import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { expect, type BrowserContext, type Page } from "@playwright/test";

import { signInThroughPage, signInWithPassword } from "./auth-support";
import { AUTH_E2E, POSTGRES_DB, POSTGRES_TEST_DB, POSTGRES_USER, ROOT_DIR } from "./env";

// Seeded by backend/app/scripts/seed_dev.py (fixed ids).
export const ORG_A = { id: "00000000-0000-4000-8000-0000000000a1", name: "Fredrik Horse Therapy" };
export const ORG_B = { id: "00000000-0000-4000-8000-0000000000b2", name: "Umeå Stable Services" };
export const FREDRIK = "fredrik@dev.test"; // owner of A, admin of B
export const MARIA = "maria@dev.test"; // employee of B only
export const RANDOM_ORG = "00000000-0000-4000-8000-00000000dead";

export const bffUrl = (orgId: string, path: string) => `/api/o/${orgId}${path}`;

/**
 * Sign in through the real route handler; the cookie lands in the browser context. In the SESSION run
 * (E2E_AUTH=session) this is the real login with a real password; the specs themselves do not change.
 */
export async function signIn(context: BrowserContext, email: string): Promise<void> {
  if (AUTH_E2E === "session") return signInWithPassword(context, email);
  const response = await context.request.post("/api/dev-session", { form: { email }, maxRedirects: 0 });
  expect(response.status()).toBe(303);
}

export async function signInViaUi(page: Page, email: string): Promise<void> {
  if (AUTH_E2E === "session") return signInThroughPage(page, email);
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
/** The organization's own date (in its time zone) as FastAPI states it: what date fields default to. */
export async function organizationToday(context: BrowserContext, orgId: string): Promise<string> {
  const response = await context.request.get(bffUrl(orgId, "/organization"));
  expect(response.status()).toBe(200);
  return ((await response.json()) as { today: string }).today;
}

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

// --- Custom fields ------------------------------------------------------------------------------------------------------

export interface DefinitionJson {
  id: string;
  key: string;
  entity_type: string;
  required: boolean;
  enabled: boolean;
  field_type: string;
}

/** The enabled definitions of an entity type, as the backend lists them. */
export async function definitionsOf(context: BrowserContext, orgId: string, entityType: string): Promise<DefinitionJson[]> {
  const response = await context.request.get(bffUrl(orgId, `/custom-fields/definitions?entity_type=${entityType}&limit=200`));
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as DefinitionJson[];
}

async function patchDefinition(context: BrowserContext, orgId: string, id: string, data: Record<string, unknown>) {
  const response = await context.request.patch(bffUrl(orgId, `/custom-fields/definitions/${id}`), { data });
  expect(response.status(), await response.text()).toBe(200);
}

/** Make the named fields of an entity type required for the duration of `body`, and ALWAYS put them back. */
export async function withRequired(context: BrowserContext, orgId: string, entityType: string, keys: string[], body: () => Promise<void>) {
  const all = await definitionsOf(context, orgId, entityType);
  const targets = all.filter((definition) => keys.includes(definition.key));
  expect(targets.map((definition) => definition.key).sort()).toEqual([...keys].sort());
  const before = targets.map((definition) => ({ id: definition.id, required: definition.required }));
  for (const target of targets) await patchDefinition(context, orgId, target.id, { required: true });
  try {
    await body();
  } finally {
    for (const original of before) await patchDefinition(context, orgId, original.id, { required: original.required });
  }
}

/**
 * A synthetic field definition, created through the real API (as an administrator would) and ALWAYS
 * disabled afterwards, so it cannot affect any other test (definitions are disabled, never deleted).
 */
export async function withDefinitions(
  context: BrowserContext,
  orgId: string,
  definitions: Record<string, unknown>[],
  body: (created: DefinitionJson[]) => Promise<void>,
) {
  const created: DefinitionJson[] = [];
  try {
    for (const data of definitions) {
      const response = await context.request.post(bffUrl(orgId, "/custom-fields/definitions"), { data });
      expect(response.status(), await response.text()).toBe(201);
      created.push((await response.json()) as DefinitionJson);
    }
    await body(created);
  } finally {
    for (const definition of created) await patchDefinition(context, orgId, definition.id, { enabled: false, required: false }).catch(() => undefined);
  }
}

/** A key nobody else uses (the backend wants lowercase letters, digits and underscores). */
export const uniqueKey = (prefix: string) => `${prefix}_${randomUUID().slice(0, 8).replaceAll("-", "")}`;

/** The id of a customer, horse or item by name in an organization (read from the TEST database). */
export function idOf(table: "customers" | "horses" | "items", orgId: string, name: string): string {
  return testRow(`select id from ${table} where organization_id = ${sql(orgId)} and name = ${sql(name)} order by created_at limit 1`);
}

/** The custom values stored for a record, as `key=value` pairs, read from the TEST database. */
export function storedValues(entityId: string): string {
  return testRow(
    `select coalesce(string_agg(d.key || '=' || coalesce(v.value_reference_id::text, v.value_text, v.value_number::text, v.value_date::text, v.value_boolean::text, v.value_option_id::text), ',' order by d.key), '') from custom_field_values v join custom_field_definitions d on d.id = v.definition_id where v.entity_id = ${sql(entityId)}`,
  );
}

// --- Throwaway organizations (settings and currency specs) ------------------------------------------------------------------

export type RoleName = "owner" | "admin" | "accountant" | "employee" | "viewer";

export interface World {
  orgId: string;
  name: string;
  /** The owner's login. */
  email: string;
  userIds: string[];
  /** A member with the given role (created on demand); returns the login email. */
  addMember: (role: RoleName) => string;
  /** Remove everything this world created from the TEST database. */
  cleanup: () => void;
}

/**
 * An organization of its own with an owner of its own, created directly in the TEST database, so a
 * spec can change settings and currencies without touching the seeded organizations (whose
 * state other specs rely on). `currency: null` is an organization that never configured one.
 */
export function createWorld(options: { currency?: string | null; label?: string } = {}): World {
  const orgId = randomUUID();
  const tag = randomUUID().slice(0, 8);
  const name = `${options.label ?? "World"} ${tag}`;
  const currency = options.currency === undefined ? "SEK" : options.currency;
  const userIds: string[] = [];
  const userEmails: string[] = [];

  const addUser = (role: RoleName, label: string): string => {
    const id = randomUUID();
    const taken = userEmails.filter((used) => used.startsWith(`${label}-${tag}`)).length;
    const email = taken === 0 ? `${label}-${tag}@dev.test` : `${label}-${tag}-${taken + 1}@dev.test`; // a second member of a role gets its own login
    userEmails.push(email);
    testRow(`insert into users (id, email, name) values (${sql(id)}, ${sql(email)}, ${sql(`${label} ${tag}`)})`);
    testRow(`insert into organization_users (organization_id, user_id, role) values (${sql(orgId)}, ${sql(id)}, ${sql(role)})`);
    userIds.push(id);
    return email;
  };

  testRow(`insert into organizations (id, name, default_currency) values (${sql(orgId)}, ${sql(name)}, ${currency === null ? "null" : sql(currency)})`);
  const email = addUser("owner", "owner");

  return {
    orgId,
    name,
    email,
    userIds,
    addMember: (role) => addUser(role, role),
    cleanup: () => {
      const org = sql(orgId);
      // One transaction with triggers and foreign keys switched off: issued invoices are protected on
      // purpose (immutability triggers, RESTRICT keys), and this is a throwaway organization in the
      // disposable TEST database. Everything the organization can own is removed.
      const tables = [
        "invoice_pdfs",
        "invoice_vat_rows",
        "invoice_lines",
        "invoice_transactions",
        "invoices",
        "invoice_counters",
        "custom_field_values",
        "custom_field_options",
        "custom_field_definitions",
        "transaction_lines",
        "transactions",
        "horses",
        "items",
        "customers",
        "organization_creation_requests",
        "organization_invitations",
        "organization_users",
        "audit_events",
      ];
      const statements = [
        "set local session_replication_role = replica",
        ...tables.map((table) => `delete from ${table} where organization_id = ${org}`),
        `delete from security_events where organization_id = ${org}${userIds.length ? ` or actor_user_id in (${userIds.map(sql).join(",")})` : ""}`,
        `delete from organizations where id = ${org}`,
        ...userIds.map((id) => `delete from users where id = ${sql(id)}`),
      ];
      testRow(statements.join("; "));
    },
  };
}

/** Insert a customer into a world's organization (test database) and return its id. */
export function insertCustomer(orgId: string, name: string): string {
  const id = randomUUID();
  testRow(`insert into customers (id, organization_id, customer_type, name) values (${sql(id)}, ${sql(orgId)}, 'company', ${sql(name)})`);
  return id;
}

/**
 * Insert a transaction that PREDATES currencies (currency NULL) with one line of 850.00 + 25 % VAT,
 * exactly as an old row would look. Directly in the test database: the API has no way to make one.
 */
export function insertCurrencylessTransaction(orgId: string, customerId: string, date = "2026-01-15"): string {
  const id = randomUUID();
  testRow(
    `insert into transactions (id, organization_id, billing_customer_id, transaction_date, status) values (${sql(id)}, ${sql(orgId)}, ${sql(customerId)}, ${sql(date)}, 'completed')`,
  );
  testRow(
    `insert into transaction_lines (organization_id, transaction_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) values (${sql(orgId)}, ${sql(id)}, 1, 'Old massage', 'session', 1, 850.00, 25.00, 850.00, 212.50, 1062.50)`,
  );
  return id;
}

// --- Invoicing ---------------------------------------------------------------------------------------------------------------

export interface InvoiceJson {
  id: string;
  status: "draft" | "issued";
  version: number;
  number: number | null;
  number_text: string | null;
  currency: string;
  customer_id: string;
  customer_name: string;
  invoice_date: string;
  due_date: string | null;
  description: string | null;
  net_amount: string;
  vat_amount: string;
  gross_amount: string;
  customer_snapshot: Record<string, unknown>;
  issuer_snapshot: Record<string, unknown>;
  transactions: { transaction_id: string; transaction_date: string; fields: unknown[] }[];
  lines: { id: string; description: string; net_amount: string; vat_amount: string; gross_amount: string; fields: { key: string; label: string; display: string | null; value: unknown }[] }[];
  vat_breakdown: { vat_rate: string; net_amount: string; vat_amount: string }[];
}

const DEFAULT_LINE = { description: "Horse massage", unit: "session", quantity: "1", unit_price_ex_vat: "850.00", vat_rate: "25" };

/** A COMPLETED transaction (created and completed through the BFF as the context's signed-in user). */
export async function createCompletedTransaction(
  context: BrowserContext,
  orgId: string,
  customerId: string,
  options: { date?: string; lines?: Record<string, string>[] } = {},
): Promise<TxJson> {
  const draft = await createTransaction(context, orgId, {
    billing_customer_id: customerId,
    transaction_date: options.date ?? "2026-10-01",
    lines: options.lines ?? [DEFAULT_LINE],
  });
  return lifecycle(context, orgId, draft.id, "complete");
}

export async function createInvoiceApi(context: BrowserContext, orgId: string, transactionIds: string[], extra: Record<string, unknown> = {}): Promise<InvoiceJson> {
  const response = await context.request.post(bffUrl(orgId, "/invoices"), { data: { transaction_ids: transactionIds, ...extra } });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as InvoiceJson;
}

export async function getInvoiceApi(context: BrowserContext, orgId: string, id: string): Promise<InvoiceJson> {
  const response = await context.request.get(bffUrl(orgId, `/invoices/${id}`));
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as InvoiceJson;
}

export async function issueInvoiceApi(context: BrowserContext, orgId: string, invoice: Pick<InvoiceJson, "id" | "version">): Promise<InvoiceJson> {
  const response = await context.request.post(bffUrl(orgId, `/invoices/${invoice.id}/issue`), { headers: ifMatch(invoice.version) });
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as InvoiceJson;
}

/** A completed transaction straight in the TEST database with the given currency (the API can only make ones in the organization's currency). */
export function insertCompletedTransaction(orgId: string, customerId: string, currency: string | null, date = "2026-10-03"): string {
  const id = randomUUID();
  testRow(
    `insert into transactions (id, organization_id, billing_customer_id, transaction_date, status, currency) values (${sql(id)}, ${sql(orgId)}, ${sql(customerId)}, ${sql(date)}, 'completed', ${currency === null ? "null" : sql(currency)})`,
  );
  testRow(
    `insert into transaction_lines (organization_id, transaction_id, position, description, unit, quantity, unit_price_ex_vat, vat_rate, net_amount, vat_amount, gross_amount) values (${sql(orgId)}, ${sql(id)}, 1, 'Direct line', 'session', 1, 100.00, 25.00, 100.00, 25.00, 125.00)`,
  );
  return id;
}

// --- Organization onboarding ---------------------------------------------------------------------------------------------------

export interface Account {
  id: string;
  email: string;
  /** Remove the account and every organization it created (test database only). */
  cleanup: () => void;
  /** The organizations this account owns because it created them through the API, newest last. */
  createdOrganizations: () => string[];
}

const ORGANIZATION_TABLES = [
  "invoice_pdfs",
  "invoice_vat_rows",
  "invoice_lines",
  "invoice_transactions",
  "invoices",
  "invoice_counters",
  "custom_field_values",
  "custom_field_options",
  "custom_field_definitions",
  "transaction_lines",
  "transactions",
  "horses",
  "items",
  "customers",
  "organization_invitations",
];

/**
 * A user of its own in the TEST database, with or without room to create organizations (`canCreate: true` = may own
 * five; `false` = may own exactly the organizations it is made owner of below, so it is at its limit),
 * optionally a member of existing organizations (a world's, say). `cleanup` also removes every organization this
 * account created through the application, with everything inside them.
 */
export function createAccount(options: { canCreate: boolean; memberships?: { orgId: string; role: RoleName }[]; label?: string }): Account {
  const id = randomUUID();
  const email = `${options.label ?? "newcomer"}-${id.slice(0, 8)}@dev.test`;
  const owned = (options.memberships ?? []).filter((membership) => membership.role === "owner").length;
  const limit = options.canCreate ? 5 : owned;
  testRow(`insert into users (id, email, name, max_owned_organizations) values (${sql(id)}, ${sql(email)}, ${sql("Newcomer")}, ${limit})`);
  for (const membership of options.memberships ?? []) {
    testRow(`insert into organization_users (organization_id, user_id, role) values (${sql(membership.orgId)}, ${sql(id)}, ${sql(membership.role)})`);
  }
  const createdOrganizations = () =>
    testRow(`select organization_id from security_events where actor_user_id = ${sql(id)} and event_type = 'organization_created' order by id`)
      .split("\n")
      .filter(Boolean);
  return {
    id,
    email,
    createdOrganizations,
    cleanup: () => {
      const created = createdOrganizations();
      const statements = [
        "set local session_replication_role = replica",
        ...created.flatMap((org) => [...ORGANIZATION_TABLES.map((table) => `delete from ${table} where organization_id = ${sql(org)}`), `delete from organization_users where organization_id = ${sql(org)}`, `delete from organizations where id = ${sql(org)}`]),
        `delete from organization_creation_requests where user_id = ${sql(id)}`,
        `delete from security_events where actor_user_id = ${sql(id)}`,
        `delete from auth_sessions where user_id = ${sql(id)}`,
        `delete from user_setup_tokens where user_id = ${sql(id)}`,
        `delete from user_credentials where user_id = ${sql(id)}`,
        `delete from organization_users where user_id = ${sql(id)}`,
        `delete from users where id = ${sql(id)}`,
      ];
      testRow(statements.join("; "));
    },
  };
}

/** The membership rows of an organization as `email:role` pairs, sorted (test database). */
export function membersOf(orgId: string): string[] {
  return testRow(`select u.email || ':' || ou.role from organization_users ou join users u on u.id = ou.user_id where ou.organization_id = ${sql(orgId)} order by 1`)
    .split("\n")
    .filter(Boolean);
}

/** How many organizations, memberships and creation requests exist right now (test database): "unchanged" proofs. */
export function onboardingCounts(): string {
  return testRow("select (select count(*) from organizations) || '/' || (select count(*) from organization_users) || '/' || (select count(*) from organization_creation_requests)");
}

/** Remove users (by email) that a spec created through the application, with their sessions, credentials and events. */
export function purgeUsersByEmail(emails: string[]): void {
  if (emails.length === 0) return;
  const list = emails.map(sql).join(",");
  const ids = `(select id from users where email in (${list}))`;
  testRow(
    [
      "set local session_replication_role = replica",
      `delete from security_events where actor_user_id in ${ids}`,
      `delete from auth_sessions where user_id in ${ids}`,
      `delete from user_credentials where user_id in ${ids}`,
      `delete from user_setup_tokens where user_id in ${ids}`,
      `delete from organization_users where user_id in ${ids}`,
      `delete from users where email in (${list})`,
    ].join("; "),
  );
}
