export interface NavItem {
  label: string;
  /** Path below /o/{orgId}; "" is the dashboard. */
  path: string;
  /** Pages arrive in later slices; until then the entry is shown but not linked. */
  enabled: boolean;
}

export const NAV: NavItem[] = [
  { label: "Dashboard", path: "", enabled: true },
  { label: "Customers", path: "/customers", enabled: true },
  { label: "Catalog", path: "/catalog", enabled: true },
  { label: "Horses", path: "/horses", enabled: true },
  { label: "Transactions", path: "/transactions", enabled: true },
  { label: "Invoices", path: "/invoices", enabled: true },
  { label: "Settings", path: "/settings", enabled: true },
];
