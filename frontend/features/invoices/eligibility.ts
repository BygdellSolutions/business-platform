import type { Invoiceable, InvoiceCreate } from "@/lib/api/types";

/**
 * Which transactions may be chosen together for one invoice. The rule mirrors what the backend
 * enforces (one billing customer, one currency); FastAPI stays the authority and answers a
 * structured conflict when the screen is out of date. Everything here is a comparison of ids and
 * currency codes. There is NO arithmetic: nothing is added up, and no combined amount exists on
 * this screen, because the backend offers no authoritative aggregate for a selection.
 */
export type Compatibility = "ok" | "different_customer" | "different_currency";

/** Can `candidate` join `selection`? An empty selection accepts anything. */
export function compatibility(selection: readonly Invoiceable[], candidate: Invoiceable): Compatibility {
  const first = selection[0];
  if (first === undefined) return "ok";
  if (candidate.billing_customer_id !== first.billing_customer_id) return "different_customer";
  if (candidate.currency !== first.currency) return "different_currency";
  return "ok";
}

export const REASON: Record<Exclude<Compatibility, "ok">, string> = {
  different_customer: "Another billing customer",
  different_currency: "Another currency",
};

/** Add or remove one transaction. A candidate that is not compatible is never added. */
export function toggle(selection: readonly Invoiceable[], row: Invoiceable): Invoiceable[] {
  if (selection.some((selected) => selected.id === row.id)) return selection.filter((selected) => selected.id !== row.id);
  if (compatibility(selection, row) !== "ok") return [...selection];
  return [...selection, row];
}

/** Forget the given transaction ids (the backend said they can no longer be invoiced). */
export function without(selection: readonly Invoiceable[], ids: readonly string[]): Invoiceable[] {
  return selection.filter((selected) => !ids.includes(selected.id));
}

export interface HeaderInput {
  invoiceDate: string;
  dueDate: string;
  description: string;
}

/**
 * The create request: the chosen transaction ids and the approved header fields, nothing else. No
 * customer, no currency, no organization, no amounts: the backend derives all of them from the
 * locked transactions. A blank header field is simply left out (the backend defaults the date).
 */
export function createBody(selection: readonly Invoiceable[], header: HeaderInput): InvoiceCreate {
  const body: InvoiceCreate = { transaction_ids: selection.map((selected) => selected.id) };
  if (header.invoiceDate !== "") body.invoice_date = header.invoiceDate;
  if (header.dueDate !== "") body.due_date = header.dueDate;
  if (header.description.trim() !== "") body.description = header.description;
  return body;
}
