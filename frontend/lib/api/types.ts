/**
 * Hand-written types for the backend responses used by the frontend (V1). Generating them
 * from FastAPI's OpenAPI document is planned later.
 *
 * Money, VAT, quantity and decimal custom-field values are NEVER typed `number`: the backend
 * sends and expects decimal strings (see lib/decimal.ts).
 */

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
