/**
 * Hand-written types for the backend responses used by the frontend (V1). Generating them
 * from FastAPI's OpenAPI document is planned later.
 *
 * Money, VAT, quantity and decimal custom-field values are NEVER typed `number`: the backend
 * sends and expects decimal strings (see lib/decimal.ts).
 */

import type { MoneyString, PercentString, QuantityString } from "@/lib/decimal";

export type Role = "owner" | "admin" | "accountant" | "employee" | "viewer";

export interface Membership {
  id: string;
  name: string;
  role: Role;
}

export type CustomerType = "person" | "company";

export interface Customer {
  id: string;
  customer_type: CustomerType;
  name: string;
  email: string | null;
  phone: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

/** What the create form sends. There is no organization_id: the backend takes it from the tenant context. */
export interface CustomerCreate {
  customer_type: CustomerType;
  name: string;
  email: string | null;
  phone: string | null;
  active: boolean;
}

/** Partial update: only the fields present are changed. */
export type CustomerUpdate = Partial<CustomerCreate>;

export type ItemType = "service" | "product";

export interface Item {
  id: string;
  type: ItemType;
  name: string;
  description: string | null;
  unit: string;
  /** Excluding VAT. A decimal STRING with two decimals as the backend formats it ("850.00"). */
  price_ex_vat: MoneyString;
  /** Percent, a decimal STRING with two decimals ("25.00"). */
  vat_rate: PercentString;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ItemCreate {
  type: ItemType;
  name: string;
  description: string | null;
  unit: string;
  price_ex_vat: MoneyString;
  vat_rate: PercentString;
  active: boolean;
}

export type ItemUpdate = Partial<ItemCreate>;

/** The compact customer the backend embeds in records that refer to one (a horse owner). */
export interface CustomerRef {
  id: string;
  name: string;
  /** False when the customer was deactivated after the reference was made. */
  active: boolean;
}

export type HorseSex = "mare" | "stallion" | "gelding";

export interface Horse {
  id: string;
  name: string;
  owner_customer_id: string;
  stable_customer_id: string | null;
  owner: CustomerRef;
  stable: CustomerRef | null;
  birth_year: number | null;
  sex: HorseSex | null;
  breed: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * What the create form sends. The owner is OPTIONAL in this type on purpose: when none is
 * chosen the field is left out and the backend answers "Field required" on it. The frontend
 * does not decide that rule. A birth year is a JSON integer (the backend refuses a string).
 */
export interface HorseCreate {
  name: string;
  owner_customer_id?: string;
  stable_customer_id: string | null;
  birth_year: number | null;
  sex: HorseSex | null;
  breed: string | null;
  active: boolean;
}

export type HorseUpdate = Partial<Omit<HorseCreate, "owner_customer_id">> & { owner_customer_id?: string };

// --- Sales: transactions and their lines --------------------------------------------------------------------
//
// Quantity, price, VAT, net, VAT amount and gross are decimal STRINGS ("1.000", "850.00"). The
// frontend never adds them up or recomputes them: line amounts, totals and the VAT breakdown are
// calculated by FastAPI and displayed exactly as received.

export type TransactionStatus = "draft" | "completed" | "cancelled";

export interface TransactionLine {
  id: string;
  transaction_id: string;
  /** Link back to the catalog, or null for an ad-hoc line. Never used to look up values: a line shows its own snapshot. */
  item_id: string | null;
  position: number;
  /** Optimistic concurrency: sent as If-Match when this line is edited or deleted. */
  version: number;
  description: string;
  unit: string;
  quantity: QuantityString;
  unit_price_ex_vat: MoneyString;
  vat_rate: PercentString;
  net_amount: MoneyString;
  vat_amount: MoneyString;
  gross_amount: MoneyString;
  created_at: string;
  updated_at: string;
}

export interface VatBreakdownRow {
  vat_rate: PercentString;
  net_amount: MoneyString;
  vat_amount: MoneyString;
}

export interface Totals {
  net_amount: MoneyString;
  vat_amount: MoneyString;
  gross_amount: MoneyString;
  vat_breakdown: VatBreakdownRow[];
}

export interface TransactionSummary {
  id: string;
  billing_customer_id: string;
  billing_customer: CustomerRef;
  /** A calendar date, "YYYY-MM-DD". */
  transaction_date: string;
  status: TransactionStatus;
  line_count: number;
  /** Optimistic concurrency: sent as If-Match for complete, reopen, cancel and deleting a draft. */
  version: number;
  /** Optimistic concurrency: sent as If-Match when the header (customer, date) is edited. */
  header_version: number;
  totals: Totals;
  created_at: string;
  updated_at: string;
}

export interface Transaction extends TransactionSummary {
  lines: TransactionLine[];
}

/** What the create form sends: the header only. Fields left out are left to the backend (the customer is then "required"). */
export interface TransactionCreate {
  billing_customer_id?: string;
  transaction_date?: string;
}

export type TransactionHeaderUpdate = Partial<TransactionCreate>;

/**
 * A new line. With `item_id` the backend copies the item and ONLY `item_id` and `quantity` are
 * sent by the catalog path; without it (an ad-hoc line) the backend requires the other four.
 */
export interface LineCreate {
  item_id?: string;
  description?: string;
  unit?: string;
  quantity: QuantityString;
  unit_price_ex_vat?: MoneyString;
  vat_rate?: PercentString;
}

/** A line edit. There is deliberately no `item_id`: this UI never re-snapshots or detaches a line. */
export type LineUpdate = Partial<Pick<LineCreate, "description" | "unit" | "quantity" | "unit_price_ex_vat" | "vat_rate">>;
