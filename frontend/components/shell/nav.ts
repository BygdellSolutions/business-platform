import type { Role } from "@/lib/api/types";

export interface NavItem {
  label: string;
  /** Roles that are shown the entry (presentation only; FastAPI authorizes every request). Everyone when absent. */
  roles?: readonly Role[];
  /** Path below /o/{orgId}; "" is the dashboard. */
  path: string;
  /** Pages arrive in later slices; until then the entry is shown but not linked. */
  enabled: boolean;
}

export const NAV: NavItem[] = [
  { label: "Dashboard", path: "", enabled: true },
  { label: "Customers", path: "/customers", enabled: true },
  { label: "Suppliers", path: "/suppliers", enabled: true },
  { label: "Catalog", path: "/catalog", enabled: true },
  { label: "Inventory", path: "/inventory", enabled: true },
  { label: "Horses", path: "/horses", enabled: true },
  { label: "Orders", path: "/transactions", enabled: true },
  { label: "Invoices", path: "/invoices", enabled: true },
  { label: "Settings", path: "/settings", enabled: true },
  { label: "Members", path: "/members", enabled: true, roles: ["owner", "admin"] },
];
