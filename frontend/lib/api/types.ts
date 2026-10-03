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

/**
 * Optional postal address and business identifiers of a customer or of the organization itself.
 * All free text; blank means "not set" (null). Only the SHAPE of the country code is checked, by
 * the backend (two capital letters).
 */
export interface Profile {
  address_line1: string | null;
  address_line2: string | null;
  postal_code: string | null;
  city: string | null;
  country_code: string | null;
  registration_number: string | null;
  vat_number: string | null;
}

export type ProfileField = keyof Profile;

export interface Customer extends Profile {
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
export interface CustomerCreate extends Profile {
  customer_type: CustomerType;
  name: string;
  email: string | null;
  phone: string | null;
  active: boolean;
}

/** Partial update: only the fields present are changed. */
export type CustomerUpdate = Partial<CustomerCreate>;

/** The active organization's settings (GET /api/organization). */
export interface Organization extends Profile {
  id: string;
  /** The display name used in the app. */
  name: string;
  /** The name to print on documents, if different. */
  legal_name: string | null;
  /** Three capital letters, or null until an owner or admin sets it. Nothing assumes a currency. */
  default_currency: string | null;
  /** True once items or transactions exist: the currency can no longer be changed. */
  default_currency_locked: boolean;
  default_currency_lock_reason: string | null;
  created_at: string;
  updated_at: string;
}

/** Partial update of the organization's settings; only the fields present are changed. */
export type OrganizationUpdate = Partial<Profile> & {
  name?: string;
  legal_name?: string | null;
  default_currency?: string;
};

/** How many transactions predate currencies (GET /api/transactions/currency-status). */
export interface CurrencyStatus {
  default_currency: string | null;
  transactions_without_currency: number;
}

export interface AssignCurrencyResult {
  currency: string;
  assigned: number;
}

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
  /** Copied from the organization when the transaction was created; never changes. Null only for a transaction that predates currencies and was not assigned one. */
  currency: string | null;
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

// --- Invoicing ------------------------------------------------------------------------------------------------------------
//
// An invoice is a stored DOCUMENT: everything on it (parties, lines, amounts, custom-field text) is
// the invoice's own snapshot. Money, quantity and VAT rates are decimal STRINGS, shown exactly as
// received; the frontend adds nothing up and recomputes nothing. Source ids are navigation metadata.

export type InvoiceStatus = "draft" | "issued";

export interface InvoiceSummary {
  id: string;
  status: InvoiceStatus;
  /** Optimistic concurrency: sent as If-Match when editing, issuing or deleting a draft. */
  version: number;
  series: string;
  /** null for a draft: a number exists only once issued. */
  number: number | null;
  number_text: string | null;
  /** Navigation metadata; the name to show is `customer_name`. */
  customer_id: string;
  customer_name: string;
  currency: string;
  /** A calendar date, "YYYY-MM-DD". */
  invoice_date: string;
  due_date: string | null;
  description: string | null;
  net_amount: MoneyString;
  vat_amount: MoneyString;
  gross_amount: MoneyString;
  transaction_count: number;
  issued_at: string | null;
  created_at: string;
  updated_at: string;
}

/** One stored custom-field value of an invoice (generic: nothing says what the field is for). */
export interface FieldSnapshot {
  key: string;
  label: string;
  field_type: "text" | "number" | "date" | "boolean" | "select" | "reference";
  /** What was stored: text, a decimal string, an ISO date, a boolean, or an id (select/reference). */
  value: string | boolean | null;
  /** The text as it resolved when the snapshot was taken; null for a reference that was already gone. */
  display: string | null;
  missing: boolean;
  position: number;
  /** For audit only; never looked up. */
  definition_id: string;
}

export interface InvoiceLine {
  id: string;
  position: number;
  source_transaction_id: string;
  source_line_id: string;
  description: string;
  unit: string;
  quantity: QuantityString;
  unit_price_ex_vat: MoneyString;
  vat_rate: PercentString;
  net_amount: MoneyString;
  vat_amount: MoneyString;
  gross_amount: MoneyString;
  fields: FieldSnapshot[];
}

export interface InvoiceSource {
  transaction_id: string;
  position: number;
  transaction_date: string;
  source_version: number;
  fields: FieldSnapshot[];
}

export interface InvoiceVatRow {
  vat_rate: PercentString;
  net_amount: MoneyString;
  vat_amount: MoneyString;
}

/** The party blocks are versioned snapshots; only fields that existed when they were taken are present. */
export interface PartySnapshot {
  schema: number;
  name: string;
  legal_name?: string | null;
  customer_type?: string;
  email?: string | null;
  phone?: string | null;
  address_line1: string | null;
  address_line2: string | null;
  postal_code: string | null;
  city: string | null;
  country_code: string | null;
  registration_number: string | null;
  vat_number: string | null;
}

export interface Invoice extends InvoiceSummary {
  issued_by: string | null;
  customer_snapshot: PartySnapshot;
  issuer_snapshot: PartySnapshot;
  transactions: InvoiceSource[];
  lines: InvoiceLine[];
  vat_breakdown: InvoiceVatRow[];
}

/** A completed, currency-bearing transaction that is on no invoice (GET /api/invoiceable-transactions). */
export interface Invoiceable {
  id: string;
  transaction_date: string;
  billing_customer_id: string;
  billing_customer: CustomerRef;
  currency: string;
  line_count: number;
  version: number;
  totals: { net_amount: MoneyString; vat_amount: MoneyString; gross_amount: MoneyString };
}

/** What the create screen sends: ids and the approved header fields only. Never a customer or a currency. */
export interface InvoiceCreate {
  transaction_ids: string[];
  invoice_date?: string;
  due_date?: string;
  description?: string;
}

/** Partial update of a draft's header; a cleared date or description is null. */
export interface InvoiceUpdate {
  invoice_date?: string;
  due_date?: string | null;
  description?: string | null;
}
