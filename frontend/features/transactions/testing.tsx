import { use, useEffect, useState } from "react";
import { vi } from "vitest";

import { OrgScope } from "@/components/shell/org-context";
import type { TransactionFields } from "@/features/transactions/editor-context";
import { TransactionEditor } from "@/features/transactions/TransactionEditor";
import { apiFetch } from "@/lib/api/client";
import { normalizeError, type ApiResult } from "@/lib/api/errors";
import type { Customer, Item, Transaction, TransactionLine } from "@/lib/api/types";
import type { MoneyString, PercentString, QuantityString } from "@/lib/decimal";
import { EMPTY_PROFILE } from "@/lib/profile";

/** Fixtures and a harness for the transaction editor's component tests (not shipped). */

export const ORG_A = "00000000-0000-4000-8000-0000000000a1";
export const ORG_B = "00000000-0000-4000-8000-0000000000b2";
export const TX_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const LINE_1 = "11111111-1111-4111-8111-111111111111";
export const LINE_2 = "22222222-2222-4222-8222-222222222222";
export const ITEM_ID = "33333333-3333-4333-8333-333333333333";
export const ANNA_ID = "44444444-4444-4444-8444-444444444444";
export const UMEA_ID = "55555555-5555-4555-8555-555555555555";

const money = (value: string) => value as MoneyString;

export function line(overrides: Partial<TransactionLine> = {}): TransactionLine {
  return {
    id: LINE_1,
    transaction_id: TX_ID,
    item_id: ITEM_ID,
    position: 1,
    version: 1,
    description: "Horse massage",
    unit: "session",
    quantity: "1.000" as QuantityString,
    unit_price_ex_vat: money("850.00"),
    list_unit_price: null,
    catalog_discount_percent: null,
    customer_discount_percent: null,
    vat_rate: "25.00" as PercentString,
    net_amount: money("850.00"),
    vat_amount: money("212.50"),
    gross_amount: money("1062.50"),
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

export function second(overrides: Partial<TransactionLine> = {}): TransactionLine {
  return line({
    id: LINE_2,
    item_id: null,
    position: 2,
    description: "Travel",
    unit: "km",
    quantity: "12.500" as QuantityString,
    unit_price_ex_vat: money("3.50"),
    list_unit_price: null,
    catalog_discount_percent: null,
    customer_discount_percent: null,
    vat_rate: "6.00" as PercentString,
    net_amount: money("43.75"),
    vat_amount: money("2.63"),
    gross_amount: money("46.38"),
    ...overrides,
  });
}

export function tx(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: TX_ID,
    billing_customer_id: ANNA_ID,
    billing_customer: { id: ANNA_ID, name: "Anna Andersson", active: true },
    transaction_date: "2026-10-01",
    status: "draft",
    currency: "SEK",
    line_count: 2,
    version: 4,
    header_version: 2,
    totals: {
      net_amount: money("893.75"),
      vat_amount: money("215.13"),
      gross_amount: money("1108.88"),
      vat_breakdown: [
        { vat_rate: "6.00" as PercentString, net_amount: money("43.75"), vat_amount: money("2.63") },
        { vat_rate: "25.00" as PercentString, net_amount: money("850.00"), vat_amount: money("212.50") },
      ],
    },
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    created_by: null,
    updated_by: null,
    lines: [line(), second()],
    ...overrides,
  };
}

export function customer(id: string, name: string, active = true): Customer {
  return { id, customer_type: "person", name, email: null, phone: null, active, created_at: "", updated_at: "", created_by: null, updated_by: null, default_discount_percent: null, ...EMPTY_PROFILE };
}

export function item(id: string, name: string, overrides: Partial<Item> = {}): Item {
  return {
    id,
    type: "service",
    name,
    description: null,
    unit: "session",
    price_ex_vat: money("850.00"),
    current_discount: null,
    vat_rate: "25.00" as PercentString,
    active: true,
    created_at: "",
    updated_at: "",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

export const ok = <T,>(data: T, status = 200): ApiResult<T> => ({ ok: true, status, data });
export const fail = <T,>(status: number, body: unknown): ApiResult<T> => ({ ok: false, error: normalizeError(status, body) });
export const stale = <T,>(currentVersion = 9): ApiResult<T> =>
  fail(409, { detail: { code: "stale_record", message: "This record was changed by someone else since you loaded it; reload it and try again", entity_type: "transaction_line", entity_id: LINE_1, current_version: currentVersion } });
export const notDraft = <T,>(): ApiResult<T> => fail(409, { detail: "A completed transaction cannot be changed; reopen it first" });
export const invalid = <T,>(...errors: [field: string, message: string][]): ApiResult<T> =>
  fail(422, { detail: errors.map(([field, msg]) => ({ loc: ["body", field], msg, type: "x" })) });

export const router = { push: vi.fn(), refresh: vi.fn() };

/**
 * Stands in for "the server re-sends the page after router.refresh()": set `server.tx` to what
 * the server has now; a refresh then re-renders the editor with it.
 */
export const server: { tx: Transaction; apply: (next: Transaction) => void; gate: Promise<void> | null } = { tx: tx(), apply: () => {}, gate: null };

export function resetServer(initial: Transaction) {
  server.tx = initial;
  server.apply = () => {};
  server.gate = null;
  router.push.mockReset();
  router.refresh.mockReset();
  router.refresh.mockImplementation(() => server.apply(server.tx));
}

/**
 * While `server.gate` is set, the page "is still loading": rendering the refreshed transaction
 * suspends until the gate opens, exactly like the real router waiting for the new page, so the
 * editor's transition stays pending.
 */
function Gate() {
  if (server.gate) use(server.gate);
  return null;
}

export const NO_FIELDS: TransactionFields = { transaction: { definitions: [], values: [] }, line: { definitions: [], values: {} } };

export function Harness({ initial, orgId = ORG_A, fields = NO_FIELDS, canEdit = true }: { initial: Transaction; orgId?: string; fields?: TransactionFields; canEdit?: boolean }) {
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
        <TransactionEditor transaction={current} fields={fields} canEdit={canEdit} />
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

// --- a fake backend for the component tests ----------------------------------------------------------------------------

export interface Call {
  method: string;
  path: string;
  body: unknown;
  ifMatch: number | undefined;
  orgId: string;
}

export const DIRECTORY = {
  customers: [customer(ANNA_ID, "Anna Andersson"), customer(UMEA_ID, "Umeå HK"), customer("66666666-6666-4666-8666-666666666666", "Old Customer", false)],
  items: [item(ITEM_ID, "Horse massage"), item("77777777-7777-4777-8777-777777777777", "Saddle fitting", { unit: "hour", price_ex_vat: money("1200.00") })],
};

/** Answers customer and item searches from DIRECTORY (honouring q and active) and everything else from `handle`. */
export function installBackend(handle: (call: Call) => ApiResult<unknown> | Promise<ApiResult<unknown>>) {
  vi.mocked(apiFetch).mockImplementation((async (orgId: string, path: string, request?: { method?: string; body?: unknown; ifMatch?: number }) => {
    const method = request?.method ?? "GET";
    if (method === "GET" && (path.startsWith("/customers?") || path.startsWith("/items?"))) {
      const url = new URL(path, "http://x");
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const activeOnly = url.searchParams.get("active") === "true";
      const rows = path.startsWith("/customers?") ? DIRECTORY.customers : DIRECTORY.items;
      return ok(rows.filter((row) => row.name.toLowerCase().includes(q) && (!activeOnly || row.active)));
    }
    return handle({ method, path, body: request?.body, ifMatch: request?.ifMatch, orgId });
  }) as typeof apiFetch);
}

/** Every request that is not a read. */
export const writes = (): Call[] =>
  vi
    .mocked(apiFetch)
    .mock.calls.map(([orgId, path, request]) => ({ method: request?.method ?? "GET", path, body: request?.body, ifMatch: request?.ifMatch, orgId }))
    .filter((call) => call.method !== "GET");

export const searches = (): string[] => vi.mocked(apiFetch).mock.calls.map(([, path]) => path).filter((path) => path.startsWith("/customers?") || path.startsWith("/items?"));
