import type { Role } from "@/lib/api/types";

export interface NavItem {
  label: string;
  /** Roles that are shown the entry (presentation only; FastAPI authorizes every request). Everyone when absent. */
  roles?: readonly Role[];
  /** Path below /o/{orgId}; "" is the dashboard. */
  path: string;
  /** Pages arrive in later slices; until then the entry is shown but not linked. */
  enabled: boolean;
  /** Heading the entry is grouped under in the sidebar; none for the dashboard. */
  section?: string;
}

export const NAV: NavItem[] = [
  { label: "Dashboard", path: "", enabled: true },
  { label: "Orders", path: "/transactions", enabled: true, section: "Sales" },
  { label: "Invoices", path: "/invoices", enabled: true, section: "Sales" },
  { label: "Scheduling", path: "/calendar", enabled: true, section: "Calendar" },
  { label: "Customers", path: "/customers", enabled: true, section: "Contacts" },
  { label: "Suppliers", path: "/suppliers", enabled: true, section: "Contacts" },
  { label: "Catalog", path: "/catalog", enabled: true, section: "Products" },
  { label: "Inventory", path: "/inventory", enabled: true, section: "Products" },
  { label: "Horses", path: "/horses", enabled: true, section: "Equine" },
  { label: "Settings", path: "/settings", enabled: true, section: "Organization" },
  { label: "Members", path: "/members", enabled: true, roles: ["owner", "admin"], section: "Organization" },
];
