import type { Role } from "@/lib/api/types";

/**
 * Who is offered the controls that change invoices. Presentation only: FastAPI authorizes every
 * request from the membership and refuses the other roles whatever the screen shows.
 */
export const INVOICING_ROLES: readonly Role[] = ["owner", "admin", "accountant"];

export function canMutateInvoices(role: Role | undefined): boolean {
  return role !== undefined && INVOICING_ROLES.includes(role);
}
