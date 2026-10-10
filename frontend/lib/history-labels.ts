import { PROFILE_LABELS } from "@/lib/profile-labels";

/**
 * How a history entry names the fields of each kind of record. Presentation only: the backend records field keys
 * and values; a key without a label here is shown as the key itself, so a new field never disappears.
 */
export const FIELD_LABELS: Record<string, Record<string, string>> = {
  customer: { customer_type: "Type", name: "Name", email: "Email", phone: "Phone", active: "Active", ...PROFILE_LABELS },
  supplier: {
    name: "Name",
    contact_person: "Contact person",
    email: "Email",
    phone: "Phone",
    our_customer_number: "Our customer number",
    active: "Active",
    ...PROFILE_LABELS,
  },
  item: { type: "Type", name: "Name", description: "Description", unit: "Unit", price_ex_vat: "Price excl. VAT", vat_rate: "VAT %", active: "Active" },
  horse_note: { body: "Note" },
  horse: { name: "Name", owner_customer_id: "Owner", stable_customer_id: "Stable", birth_year: "Birth year", sex: "Sex", breed: "Breed", active: "Active" },
  transaction: { billing_customer_id: "Billing customer", transaction_date: "Date", status: "Status", currency: "Currency" },
  transaction_line: {
    item_id: "Catalog item",
    description: "Description",
    unit: "Unit",
    quantity: "Quantity",
    unit_price_ex_vat: "Unit price",
    vat_rate: "VAT %",
    net_amount: "Net",
    vat_amount: "VAT",
    gross_amount: "Gross",
    transaction_id: "Order",
  },
  invoice: {
    payment: "Payment",
    outstanding: "Outstanding",
    refund: "Refund",
    refund_due: "Refund due",
    credit_note: "Credit note",
    return: "Return",
    status: "Status",
    number_text: "Number",
    customer_name: "Customer",
    currency: "Currency",
    invoice_date: "Invoice date",
    due_date: "Due date",
    description: "Description",
    net_amount: "Net",
    vat_amount: "VAT",
    gross_amount: "Total",
    issued_at: "Issued at",
  },
};

export const ENTITY_LABELS: Record<string, string> = {
  customer: "Customer",
  supplier: "Supplier",
  item: "Item",
  horse: "Horse",
  horse_note: "Note",
  transaction: "Order",
  transaction_line: "Line",
  invoice: "Invoice",
};

const ACTIONS: Record<string, string> = {
  created: "Created",
  updated: "Changed",
  deleted: "Deleted",
  completed: "Completed",
  reopened: "Reopened",
  cancelled: "Cancelled",
  issued: "Issued",
  fields_updated: "Custom fields changed",
  payment_recorded: "Payment recorded",
  payment_reversed: "Payment reversed",
};

export function actionLabel(action: string): string {
  return ACTIONS[action] ?? action;
}

/** Fields whose values are ids of other records, and which kind of record they point at. */
export const REFERENCE_FIELDS: Record<string, "customers" | "items"> = {
  owner_customer_id: "customers",
  stable_customer_id: "customers",
  billing_customer_id: "customers",
  item_id: "items",
};
