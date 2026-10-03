/**
 * Hand-written types for the backend responses used by the frontend (V1). Generating them
 * from FastAPI's OpenAPI document is planned later.
 *
 * Money, VAT, quantity and decimal custom-field values are NEVER typed `number`: the backend
 * sends and expects decimal strings (see lib/decimal.ts).
 */

import type { MoneyString, PercentString } from "@/lib/decimal";

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
