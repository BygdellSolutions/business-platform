import type { PickerSearch } from "@/components/ui/EntityPicker";
import { apiFetch } from "@/lib/api/client";
import type { Supplier } from "@/lib/api/types";

/** How many choices one search shows; typing narrows it. */
export const SUPPLIER_CHOICES = 20;

/**
 * Searches the suppliers of `orgId` through the BFF. `activeOnly` is for naming a supplier on something new (only active
 * suppliers may be chosen); which suppliers an organization has is decided by the backend: this only asks.
 */
export function supplierSearch(orgId: string, options: { activeOnly: boolean }): PickerSearch {
  return async (query, signal) => {
    const params = new URLSearchParams({ limit: String(SUPPLIER_CHOICES) });
    if (options.activeOnly) params.set("active", "true");
    if (query !== "") params.set("q", query);

    const result = await apiFetch<Supplier[]>(orgId, `/suppliers?${params.toString()}`, { signal });
    if (!result.ok) return result;
    return {
      ok: true,
      status: result.status,
      data: result.data.map((supplier) => ({
        id: supplier.id,
        label: supplier.name,
        detail: supplier.contact_person ?? supplier.email ?? undefined,
        inactive: !supplier.active,
      })),
    };
  };
}
