import type { Role } from "@/lib/api/types";

/**
 * Who is offered the controls that change invoices. Presentation only: FastAPI authorizes every
 * request from the membership and refuses the other roles whatever the screen shows.
 */
export const INVOICING_ROLES: readonly Role[] = ["owner", "admin", "accountant"];

export function canMutateInvoices(role: Role | undefined): boolean {
  return role !== undefined && INVOICING_ROLES.includes(role);
}

/**
 * Who is offered the controls that create, change and delete business records (customers, catalog
 * items, horses, transactions and their custom values): everyone but a viewer. Presentation only, like
 * the rule above; FastAPI refuses a viewer's write whatever the screen shows.
 */
export const RECORD_WRITER_ROLES: readonly Role[] = ["owner", "admin", "accountant", "employee"];

export function canWriteRecords(role: Role | undefined): boolean {
  return role !== undefined && RECORD_WRITER_ROLES.includes(role);
}
