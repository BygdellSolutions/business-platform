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

/** One member of the active organization, as membership administration shows them (no user id, credentials or sessions). */
export interface Member {
  /** The MEMBERSHIP id: the handle for a change or removal, meaningful only inside this organization. */
  id: string;
  name: string;
  email: string;
  role: Role;
  is_you: boolean;
}

/** A pending (or expired, not yet superseded) invitation as administration lists it. Never carries the token. */
export interface Invitation {
  id: string;
  email: string;
  role: Role;
  created_at: string;
  expires_at: string;
  state: "pending" | "expired" | "accepted" | "revoked";
  /** Who invited (names as they are now; null: not recorded) and how the invitation ended. */
  invited_by?: string | null;
  invited_by_name?: string | null;
  accepted_at?: string | null;
  accepted_by_name?: string | null;
  revoked_at?: string | null;
}

/** The response to creating or regenerating an invitation: the ONLY place the secret appears, once. */
export interface InvitationCreated extends Invitation {
  token: string;
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
  /** Permanent discount in percent (null: none). Set by owners and admins. */
  default_discount_percent: PercentString | null;
  created_at: string;
  updated_at: string;
  /** Who created it and who changed it last (user ids); null = not recorded (before authors were kept). */
  created_by: string | null;
  updated_by: string | null;
}

/** What the create form sends. There is no organization_id: the backend takes it from the tenant context. */
export interface CustomerCreate extends Profile {
  customer_type: CustomerType;
  name: string;
  email: string | null;
  phone: string | null;
  active: boolean;
  default_discount_percent?: string | null;
}

/** Partial update: only the fields present are changed. */
export type CustomerUpdate = Partial<CustomerCreate>;

/** A supplier: someone the organization buys goods from (GET /api/suppliers). */
export interface Supplier extends Profile {
  id: string;
  name: string;
  contact_person: string | null;
  email: string | null;
  phone: string | null;
  /** The organization's own customer number at the supplier. */
  our_customer_number: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

export interface SupplierCreate extends Profile {
  name: string;
  contact_person: string | null;
  email: string | null;
  phone: string | null;
  our_customer_number: string | null;
  active: boolean;
}

export type SupplierUpdate = Partial<SupplierCreate>;

/** A supplier named by another record (an incoming delivery). */
export interface SupplierRef {
  id: string;
  name: string;
  active: boolean;
}

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
  /** An IANA time zone such as Europe/Stockholm, or null: dates then default to UTC. */
  timezone: string | null;
  /** The organization's current date (YYYY-MM-DD) in its time zone: the default for new dates. */
  today: string;
  /** Contact and payment details for documents (all optional; IBAN and BIC as the backend normalized them). */
  phone: string | null;
  email: string | null;
  website: string | null;
  bankgiro: string | null;
  plusgiro: string | null;
  iban: string | null;
  bic: string | null;
  /** Days from the invoice date to the due date, used when an invoice is created without one. */
  payment_terms_days: number | null;
  /** Approved for F-tax (F-skatt); null: not stated. */
  approved_for_f_tax: boolean | null;
  /** The language of documents (invoice PDFs); null: English. */
  document_language: DocumentLanguage | null;
  created_at: string;
  updated_at: string;
}

export type DocumentLanguage = "sv" | "en";
export type SellerTextField = "phone" | "email" | "website" | "bankgiro" | "plusgiro" | "iban" | "bic";

/**
 * The body of organization creation. There is deliberately no owner, user, role or id: the backend makes the
 * authenticated user the owner. The currency is required and chosen by the person (no default).
 */
export interface OrganizationCreate {
  name: string;
  default_currency: string;
}

/** Partial update of the organization's settings; only the fields present are changed. */
export type OrganizationUpdate = Partial<Profile> & {
  name?: string;
  legal_name?: string | null;
  default_currency?: string;
  timezone?: string | null;
  payment_terms_days?: number | null;
  approved_for_f_tax?: boolean | null;
  document_language?: DocumentLanguage | null;
} & Partial<Record<SellerTextField, string | null>>;

/** How many transactions predate currencies (GET /api/transactions/currency-status). */
export interface CurrencyStatus {
  default_currency: string | null;
  transactions_without_currency: number;
}

export interface AssignCurrencyResult {
  currency: string;
  assigned: number;
}

/** A charge is travel, mileage, a fee...: billed like an item, never stock, kept apart from products. */
export type ItemType = "service" | "product" | "charge";

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
  /** Article number (unique within the organization), if any. */
  sku: string | null;
  /** Whether the Inventory module keeps a stock ledger for this product (never true for a service). */
  track_stock: boolean;
  /** Below this quantity on hand the product is "low stock" (null: no threshold). */
  low_stock_threshold: QuantityString | null;
  /** The temporary discount active today, if any (the price above never changes). */
  current_discount: ItemDiscount | null;
  /** Computed by the backend (never here): the base price incl. VAT, and while a temporary discount runs, the
   * promotion price excl. and incl. VAT (null without one). Customer discounts are not included. */
  price_inc_vat: MoneyString;
  promotion_price_ex_vat: MoneyString | null;
  promotion_price_inc_vat: MoneyString | null;
  created_at: string;
  updated_at: string;
  /** Who created it and who changed it last (user ids); null = not recorded (before authors were kept). */
  created_by: string | null;
  updated_by: string | null;
}

export interface ItemCreate {
  type: ItemType;
  name: string;
  description: string | null;
  unit: string;
  /** Exactly one of the two; a price incl. VAT is stored as the nearest price excl. VAT. */
  price_ex_vat?: MoneyString;
  price_inc_vat?: MoneyString;
  vat_rate: PercentString;
  active: boolean;
  sku?: string | null;
  track_stock?: boolean;
  low_stock_threshold?: QuantityString | null;
}

export type ItemUpdate = Partial<ItemCreate>;

/** One change of an item's physical stock (GET /api/items/{id}/stock), newest first. */
export interface StockMovement {
  id: string;
  reason: "opening" | "adjustment" | "receipt" | "delivery" | "return";
  quantity_change: QuantityString;
  quantity_before: QuantityString;
  quantity_after: QuantityString;
  note: string | null;
  transaction_id: string | null;
  created_at: string;
  created_by: string | null;
  created_by_name: string | null;
}

/** On hand and available for an item that tracks stock (GET /api/inventory/availability). */
export interface ItemAvailability {
  item_id: string;
  on_hand: QuantityString;
  /** On open draft sales: meant for a customer, not a done deal (completion decides). */
  allocated: QuantityString;
  /** Promised to open backorders (completed sales still waiting). */
  committed: QuantityString;
  /** On hand minus allocated minus committed, never below zero. */
  available: QuantityString;
  /** On its way: open incoming deliveries not received yet. */
  incoming: QuantityString;
  low_stock_threshold: QuantityString | null;
  /** Separate states that can hold together (out of stock and low stock exclude each other). */
  states: ("out_of_stock" | "low_stock" | "backordered" | "incoming")[];
}

/** What a transaction asks of one stock-tracking item, summed over its lines (GET /api/inventory/transactions/{id}). */
export interface StockDemand {
  item_id: string;
  requested: QuantityString;
  on_hand: QuantityString;
  /** On OTHER open drafts. */
  allocated: QuantityString;
  /** What this draft can count on: on hand minus backorders minus other drafts. */
  available: QuantityString;
  incoming: QuantityString;
  /** "0.000" when there is enough; otherwise what completion would backorder. */
  shortage: QuantityString;
}

/** What completion did with one stock-tracking line (GET /api/inventory/transactions/{id}/fulfillment). */
export interface LineFulfillment {
  transaction_line_id: string;
  item_id: string;
  ordered: QuantityString;
  delivered: QuantityString;
  backordered: QuantityString;
  fulfilled_later: QuantityString;
  remaining: QuantityString;
  state: "waiting_for_stock" | "partially_fulfilled" | "ready_to_fulfill" | "fulfilled" | "cancelled";
}

/** A product that tracks stock, with its figures (GET /api/inventory/items). */
export interface StockItem extends ItemAvailability {
  name: string;
  sku: string | null;
  unit: string;
  active: boolean;
}

/** A delivery on its way (GET /api/inventory/incoming). */
export interface Incoming {
  id: string;
  item_id: string;
  item_name: string;
  item_unit: string;
  quantity: QuantityString;
  received: QuantityString;
  remaining: QuantityString;
  expected_on: string | null;
  supplier: SupplierRef | null;
  reference: string | null;
  /** Per unit, excl. VAT, in the organization's currency (null: not recorded). */
  unit_cost: MoneyString | null;
  state: "expected" | "partially_received" | "received" | "cancelled";
  /** When the delivery was recorded: shown as its order date. */
  created_at: string;
  created_by_name: string | null;
  cancelled_at: string | null;
}

/** A completed sale's units still waiting for stock (GET /api/inventory/backorders), oldest first. */
export interface Backorder {
  fulfillment_id: string;
  transaction_id: string;
  transaction_line_id: string;
  transaction_date: string;
  customer_name: string | null;
  item_id: string;
  item_name: string;
  item_unit: string;
  backordered: QuantityString;
  fulfilled_later: QuantityString;
  remaining: QuantityString;
  state: LineFulfillment["state"];
  created_at: string;
}

/** How the stock on hand WOULD be shared, oldest first (GET /api/inventory/items/{id}/allocation). */
export interface AllocationProposal {
  item_id: string;
  on_hand: QuantityString;
  proposals: { fulfillment_id: string; transaction_id: string; remaining: QuantityString; proposed: QuantityString }[];
}

export interface Stock {
  item_id: string;
  track_stock: boolean;
  on_hand: QuantityString;
  movements: StockMovement[];
}

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
  /** Who created it and who changed it last (user ids); null = not recorded (before authors were kept). */
  created_by: string | null;
  updated_by: string | null;
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
  /** Discount layers as stored (null: ad-hoc or a typed price). The unit price above is the result. */
  list_unit_price: MoneyString | null;
  catalog_discount_percent: PercentString | null;
  customer_discount_percent: PercentString | null;
  /** The line's own discount (the last layer). */
  line_discount_percent: PercentString | null;
  /** The price was typed by a person (no catalog or customer layer). */
  priced_by_hand: boolean;
  /** The unit price the line's own discount applies to: what is edited next to the discount. */
  price_before_line_discount: MoneyString | null;
  vat_rate: PercentString;
  net_amount: MoneyString;
  vat_amount: MoneyString;
  gross_amount: MoneyString;
  created_at: string;
  updated_at: string;
  /** Who created it and who changed it last (user ids); null = not recorded (before authors were kept). */
  created_by: string | null;
  updated_by: string | null;
  /** "standard" (a catalog item or an ad-hoc line) or "service" (work performed for a subject). */
  kind: "standard" | "service";
  /** Service lines: when (ISO), by whom (user id), for whom (registry key and id), and notes (any line). */
  performed_at: string | null;
  performed_by: string | null;
  subject_type: string | null;
  subject_id: string | null;
  notes: string | null;
  /** Resolved by FastAPI when read: the subject's current name and the performer's name. */
  subject_label: string | null;
  performed_by_name: string | null;
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
  /** Who created it and who changed it last (user ids); null = not recorded (before authors were kept). */
  created_by: string | null;
  updated_by: string | null;
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
  /** The line's own discount in percent (optional; the last discount layer). */
  line_discount_percent?: PercentString | null;
  kind?: "standard" | "service";
  /** "YYYY-MM-DDTHH:MM" without an offset: the organization's local time. Omitted: now. */
  performed_at?: string;
  performed_by_user_id?: string | null;
  subject_type?: string;
  subject_id?: string;
  notes?: string | null;
}

/** A line edit. There is deliberately no `item_id`: this UI never re-snapshots or detaches a line. */
export type LineUpdate = Partial<
  Pick<LineCreate, "item_id" | "description" | "unit" | "quantity" | "unit_price_ex_vat" | "vat_rate" | "line_discount_percent" | "performed_at" | "performed_by_user_id" | "subject_type" | "subject_id" | "notes">
>;

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
  /** Issued invoices only (null for a draft): the sum of recorded payments, what is left, and the state. */
  paid_amount: MoneyString | null;
  outstanding_amount: MoneyString | null;
  payment_status: "unpaid" | "partially_paid" | "paid" | null;
  /** Issued invoices only: the credited gross, whether all or part is credited, and what was paid beyond what is owed. */
  credited_amount: MoneyString | null;
  credit_status: "partly_credited" | "credited" | null;
  refund_due_amount: MoneyString | null;
  /** Return cases not closed yet (requested, goods received or approved). */
  open_returns: number;
}

/** A payment recorded by hand, a refund (money paid back, negative), or a reversal of either (naming what it cancels). */
export interface InvoicePayment {
  id: string;
  kind: "payment" | "refund" | "reversal";
  amount: MoneyString;
  paid_on: string;
  method: string;
  reference: string | null;
  note: string | null;
  reverses_payment_id: string | null;
  reversed: boolean;
  created_at: string;
  created_by_name: string | null;
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
  /** Discount layers as stored (null: ad-hoc or a typed price). The unit price above is the result. */
  list_unit_price: MoneyString | null;
  catalog_discount_percent: PercentString | null;
  customer_discount_percent: PercentString | null;
  line_discount_percent: PercentString | null;
  vat_rate: PercentString;
  net_amount: MoneyString;
  vat_amount: MoneyString;
  gross_amount: MoneyString;
  fields: FieldSnapshot[];
  /** For a service line: what the invoice keeps of the service (a snapshot; never resolved again). */
  service: InvoiceService | null;
  /** How much of the line credit notes have credited so far. */
  credited_quantity: QuantityString;
  /** What is left to credit of the line (computed by the backend). */
  creditable_quantity: QuantityString | null;
  /** How much can still go back into stock on a credit note; null when the line has no stock to return. */
  stock_returnable: QuantityString | null;
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
  /** Seller only (issuer snapshot schema 3): who issued the invoice, by name at that moment. */
  our_reference?: string | null;
}

/** A credit note (kreditfaktura) as listed on its invoice. Amounts are what is credited (positive). */
export interface CreditNoteSummary {
  id: string;
  number_text: string;
  credit_date: string;
  reason: string;
  currency: string;
  net_amount: MoneyString;
  vat_amount: MoneyString;
  gross_amount: MoneyString;
  issued_at: string;
  issued_by_name: string | null;
}

export type ReturnState = "requested" | "goods_received" | "approved" | "rejected" | "credited";

/** A return case on an issued invoice: the work before a credit note (closed by it, or rejected). */
export interface InvoiceReturn {
  id: string;
  state: ReturnState;
  reason: string;
  follow_up_on: string;
  rejection_reason: string | null;
  credit_note_id: string | null;
  created_at: string;
  lines: { invoice_line_id: string; description: string; unit: string; quantity: QuantityString; returned_to_stock: boolean }[];
  events: { kind: "opened" | "note" | "goods_received" | "approved" | "rejected" | "credited" | "follow_up"; note: string | null; created_at: string; created_by_name: string | null }[];
}

export interface Invoice extends InvoiceSummary {
  payments: InvoicePayment[];
  credit_notes: CreditNoteSummary[];
  returns: InvoiceReturn[];
  issued_by: string | null;
  created_by: string | null;
  updated_by: string | null;
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

/** One change to a record (GET /api/history). Values are the backend's strings, shown as received. */
export interface HistoryChange {
  from: string | number | boolean | null;
  to: string | number | boolean | null;
  /** Present for custom fields: the field's label at the time of the change. */
  label?: string;
}

export interface Person {
  id: string;
  name: string;
}

export interface HistoryEvent {
  id: number;
  occurred_at: string;
  actor: Person | null;
  entity_type: string;
  entity_id: string;
  action: string;
  changes: Record<string, HistoryChange>;
}

export interface History {
  events: HistoryEvent[];
  people: Person[];
}

/** A temporary catalog discount (GET /api/items/{id}/discounts). Dates are inclusive; no end means open-ended. */
export interface ItemDiscount {
  id: string;
  item_id: string;
  percent: PercentString;
  starts_on: string;
  ends_on: string | null;
  note: string | null;
  created_at: string;
  created_by: string | null;
}

export interface InvoiceService {
  performed_at: string;
  /** The performed time as the organization reads it ("2026-10-03 14:00"). */
  performed_at_local: string;
  performed_by: string | null;
  subject_type: string;
  subject_label: string | null;
  notes: string | null;
}

/** A service performed (GET /api/transactions/services), for a record's page. */
export interface ServiceRecord {
  transaction_id: string;
  transaction_date: string;
  status: "draft" | "completed" | "cancelled";
  currency: string | null;
  line_id: string;
  description: string;
  quantity: QuantityString;
  gross_amount: MoneyString;
  performed_at: string;
  performed_by_name: string | null;
  subject_type: string;
  subject_id: string;
  subject_label: string | null;
  notes: string | null;
}

/** A non-service line billed to a customer (GET /api/transactions/bought): a catalog item or an ad-hoc line. */
export interface BoughtLine {
  transaction_id: string;
  transaction_date: string;
  status: TransactionStatus;
  currency: string | null;
  line_id: string;
  /** null: an ad-hoc line. */
  item_id: string | null;
  description: string;
  unit: string;
  quantity: QuantityString;
  unit_price_ex_vat: MoneyString;
  gross_amount: MoneyString;
}

/** Whether an order is on an invoice (GET /api/invoices/by-transaction). */
export interface InvoiceStateOfOrder {
  transaction_id: string;
  state: "none" | "draft" | "invoiced";
  invoice_id: string | null;
  number_text: string | null;
}

/** A member's name as any member may see it (GET /api/members/people). */
export interface Colleague {
  user_id: string;
  name: string;
}

/** A sum in ONE currency (summaries never add amounts of different currencies). */
export interface CurrencyAmount {
  currency: string;
  amount: MoneyString;
}

export interface CountAndAmounts {
  count: number;
  amounts: CurrencyAmount[];
}

/** GET /api/transactions/summary: the organization's month, in its time zone. */
export interface SalesSummary {
  month_start: string;
  /** The last day counted: the month's last day, or today for the current month. */
  month_end: string;
  /** "YYYY-MM" of the month shown and its neighbours (no next month after the current one). */
  month: string;
  previous_month: string;
  next_month: string | null;
  /** The year of the month shown: from 1 January to its end, or to today for the current year. */
  year: number;
  year_start: string;
  year_end: string;
  completed_this_year: CountAndAmounts;
  services_this_year: number;
  today: string;
  drafts: number;
  completed_this_month: CountAndAmounts;
  services_this_month: number;
}

/** GET /api/invoices/summary. Payments are not tracked yet: "past due" counts every issued invoice past its due date. */
export interface InvoicingSummary {
  month_start: string;
  month_end: string;
  ready_to_invoice: CountAndAmounts;
  draft_invoices: number;
  /** Issued and not fully paid; of those, the ones not yet due and the partially paid ones (amounts outstanding). */
  unpaid: CountAndAmounts;
  not_yet_due: CountAndAmounts;
  partially_paid: CountAndAmounts;
  /** Paid beyond what is owed after credit notes (the amounts are what is to be paid back). */
  refund_due: CountAndAmounts;
  /** Return cases still open, and of those the ones whose follow-up date has come. */
  returns_open: number;
  returns_follow_up_due: number;
  issued_this_month: CountAndAmounts;
  /** Issued, past the due date and not fully paid (the amounts are what is outstanding). */
  past_due: CountAndAmounts;
  /** Payments dated this month (reversals subtracted). */
  paid_this_month: CountAndAmounts;
  issued_this_year: CountAndAmounts;
  paid_this_year: CountAndAmounts;
}

/** GET /api/inventory/summary: active products that track stock. */
export interface InventorySummary {
  tracked_items: number;
  out_of_stock: number;
  low_stock: number;
  open_backorders: number;
  backordered_items: number;
  incoming_deliveries: number;
}

/** A note on a horse (GET /api/horses/{id}/notes), newest first. */
export interface HorseNote {
  id: string;
  horse_id: string;
  body: string;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
  created_by_name: string | null;
  updated_by_name: string | null;
}
