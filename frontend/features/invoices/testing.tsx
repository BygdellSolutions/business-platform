import { use, useEffect, useState } from "react";
import { vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import { InvoiceView } from "@/features/invoices/InvoiceView";
import { apiFetch } from "@/lib/api/client";
import { normalizeError, type ApiResult } from "@/lib/api/errors";
import type { FieldSnapshot, Invoice, InvoiceLine, Invoiceable, PartySnapshot } from "@/lib/api/types";
import type { MoneyString, PercentString, QuantityString } from "@/lib/decimal";

/** Fixtures and a harness for the invoice screens' component tests (not shipped). */

export const ORG_A = "00000000-0000-4000-8000-0000000000a1";
export const ORG_B = "00000000-0000-4000-8000-0000000000b2";
export const INVOICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const CUSTOMER_ID = "44444444-4444-4444-8444-444444444444";
export const TX_1 = "11111111-1111-4111-8111-111111111111";
export const TX_2 = "22222222-2222-4222-8222-222222222222";
export const LINE_1 = "55555555-5555-4555-8555-555555555551";
export const LINE_2 = "55555555-5555-4555-8555-555555555552";

const money = (value: string) => value as MoneyString;
const percent = (value: string) => value as PercentString;
const quantity = (value: string) => value as QuantityString;

export function party(overrides: Partial<PartySnapshot> = {}): PartySnapshot {
  return {
    schema: 1,
    name: "Umeå HK",
    address_line1: "Ridvägen 2",
    address_line2: null,
    postal_code: "903 30",
    city: "Umeå",
    country_code: "SE",
    registration_number: "802000-0001",
    vat_number: "SE802000000101",
    ...overrides,
  };
}

export function snapshot(overrides: Partial<FieldSnapshot> = {}): FieldSnapshot {
  return { key: "note", label: "Note", field_type: "text", value: "Handle with care", display: "Handle with care", missing: false, position: 10, definition_id: "d-note", ...overrides };
}

export function line(overrides: Partial<InvoiceLine> = {}): InvoiceLine {
  return {
    id: LINE_1,
    position: 1,
    source_transaction_id: TX_1,
    source_line_id: "66666666-6666-4666-8666-666666666661",
    description: "Horse massage",
    unit: "session",
    quantity: quantity("1.000"),
    unit_price_ex_vat: money("850.00"),
    list_unit_price: null,
    catalog_discount_percent: null,
    customer_discount_percent: null,
    line_discount_percent: null,
    vat_rate: percent("25.00"),
    net_amount: money("850.00"),
    vat_amount: money("212.50"),
    gross_amount: money("1062.50"),
    service: null,
    fields: [],
    ...overrides,
  };
}

/** A consistent draft. Override fields to build an issued, stale or deliberately inconsistent one. */
export function invoice(overrides: Partial<Invoice> = {}): Invoice {
  return {
    id: INVOICE_ID,
    status: "draft",
    version: 3,
    series: "default",
    number: null,
    number_text: null,
    customer_id: CUSTOMER_ID,
    customer_name: "Umeå HK",
    currency: "SEK",
    paid_amount: null,
    outstanding_amount: null,
    payment_status: null,
    payments: [],
    invoice_date: "2026-10-01",
    due_date: "2026-10-31",
    description: "October work",
    net_amount: money("893.75"),
    vat_amount: money("215.13"),
    gross_amount: money("1108.88"),
    transaction_count: 1,
    issued_at: null,
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    created_by: null,
    updated_by: null,
    issued_by: null,
    customer_snapshot: party(),
    issuer_snapshot: party({ name: "Fredrik Horse Therapy", legal_name: "Fredrik Horse Therapy AB", address_line1: "Storgatan 1", city: "Umeå", postal_code: "903 26", registration_number: "556000-0001", vat_number: "SE556000000101" }),
    transactions: [{ transaction_id: TX_1, position: 1, transaction_date: "2026-09-30", source_version: 1, fields: [] }],
    lines: [
      line(),
      line({
        id: LINE_2,
        position: 2,
        description: "Travel",
        unit: "km",
        quantity: quantity("7.001"),
        unit_price_ex_vat: money("6.25"),
        list_unit_price: null,
        catalog_discount_percent: null,
        customer_discount_percent: null,
        line_discount_percent: null,
        vat_rate: percent("6.00"),
        net_amount: money("43.75"),
        vat_amount: money("2.63"),
        gross_amount: money("46.38"),
      }),
    ],
    vat_breakdown: [
      { vat_rate: percent("6.00"), net_amount: money("43.75"), vat_amount: money("2.63") },
      { vat_rate: percent("25.00"), net_amount: money("850.00"), vat_amount: money("212.50") },
    ],
    ...overrides,
  };
}

export function issued(overrides: Partial<Invoice> = {}): Invoice {
  return invoice({ status: "issued", version: 4, number: 7, number_text: "7", issued_at: "2026-10-02T09:00:00Z", issued_by: "99999999-9999-4999-8999-999999999999", ...overrides });
}

export function eligible(id: string, overrides: Partial<Invoiceable> = {}): Invoiceable {
  return {
    id,
    transaction_date: "2026-10-01",
    billing_customer_id: CUSTOMER_ID,
    billing_customer: { id: CUSTOMER_ID, name: "Umeå HK", active: true },
    currency: "SEK",
    line_count: 1,
    version: 2,
    totals: { net_amount: money("850.00"), vat_amount: money("212.50"), gross_amount: money("1062.50") },
    ...overrides,
  };
}

export const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });
export const fail = <T,>(status: number, body: unknown): ApiResult<T> => ({ ok: false, error: normalizeError(status, body) });
export const network = <T,>(): ApiResult<T> => ({ ok: false, error: { kind: "network", status: 0, message: "Could not reach the server. Check your connection and try again." } });
export const conflict = <T,>(code: string, message: string, extra: Record<string, unknown> = {}): ApiResult<T> => fail(409, { detail: { code, message, ...extra } });
export const stale = <T,>(currentVersion = 9): ApiResult<T> => conflict("stale_record", "This record was changed by someone else since you loaded it; reload it and try again", { entity_type: "invoice", entity_id: INVOICE_ID, current_version: currentVersion });
export const alreadyIssued = <T,>(): ApiResult<T> => conflict("invoice_issued", "An issued invoice cannot be changed");
export const invalid = <T,>(...errors: [field: string, message: string][]): ApiResult<T> => fail(422, { detail: errors.map(([field, msg]) => ({ loc: ["body", field], msg, type: "x" })) });

export const router = { push: vi.fn(), refresh: vi.fn() };

/**
 * Stands in for "the server re-sends the page after router.refresh()": set `server.invoice` to what
 * the server has now; a refresh then re-renders the view with it.
 */
export const server: { invoice: Invoice; apply: (next: Invoice) => void; gate: Promise<void> | null } = { invoice: invoice(), apply: () => {}, gate: null };

export function resetServer(initial: Invoice) {
  server.invoice = initial;
  server.apply = () => {};
  server.gate = null;
  router.push.mockReset();
  router.refresh.mockReset();
  router.refresh.mockImplementation(() => server.apply(server.invoice));
}

/** While `server.gate` is set the page "is still loading": the refreshed invoice suspends until it opens. */
function Gate() {
  if (server.gate) use(server.gate);
  return null;
}

export function Harness({ initial, orgId = ORG_A, canMutate = true }: { initial: Invoice; orgId?: string; canMutate?: boolean }) {
  const [current, setCurrent] = useState(initial);
  useEffect(() => {
    server.apply = setCurrent;
    return () => {
      server.apply = () => {};
    };
  }, []);
  return (
    <>
      <Gate />
      <OrgScope orgId={orgId}>
        <InvoiceView invoice={current} canMutate={canMutate} />
      </OrgScope>
    </>
  );
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

export interface Call {
  method: string;
  path: string;
  body: unknown;
  ifMatch: number | undefined;
  orgId: string;
}

/** Answers every request through `handle` (a fake backend). */
export function installBackend(handle: (call: Call) => ApiResult<unknown> | Promise<ApiResult<unknown>>) {
  vi.mocked(apiFetch).mockImplementation((async (orgId: string, path: string, request?: { method?: string; body?: unknown; ifMatch?: number }) => {
    return handle({ method: request?.method ?? "GET", path, body: request?.body, ifMatch: request?.ifMatch, orgId });
  }) as typeof apiFetch);
}

export const calls = (): Call[] =>
  vi.mocked(apiFetch).mock.calls.map(([orgId, path, request]) => ({ method: request?.method ?? "GET", path, body: request?.body, ifMatch: request?.ifMatch, orgId }));

/** Every request that is not a read. */
export const writes = (): Call[] => calls().filter((call) => call.method !== "GET");
