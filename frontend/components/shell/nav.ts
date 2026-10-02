export interface NavItem {
  label: string;
  /** Path below /o/{orgId}; "" is the dashboard. */
  path: string;
  /** Pages arrive in later slices; until then the entry is shown but not linked. */
  enabled: boolean;
}

export const NAV: NavItem[] = [
  { label: "Dashboard", path: "", enabled: true },
  { label: "Customers", path: "/customers", enabled: false },
  { label: "Catalog", path: "/catalog", enabled: false },
  { label: "Horses", path: "/horses", enabled: false },
  { label: "Transactions", path: "/transactions", enabled: false },
];
