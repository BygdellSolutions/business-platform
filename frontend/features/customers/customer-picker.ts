import type { PickerEntity, PickerSearch } from "@/components/ui/EntityPicker";
import { apiFetch } from "@/lib/api/client";
import type { Customer, CustomerRef } from "@/lib/api/types";

/** How many choices one search shows; typing narrows it. */
export const CUSTOMER_CHOICES = 20;

/** A customer reference embedded in another record (a horse owner), as the picker shows it. */
export function customerEntity(ref: CustomerRef): PickerEntity {
  return { id: ref.id, label: ref.name, inactive: !ref.active };
}

/**
 * Searches the customers of `orgId` through the BFF. `activeOnly` is for choosing a customer
 * for a NEW assignment (only active customers may be assigned); a filter passes false so it can
 * also find records of a customer that has since been deactivated. Which customers an
 * organization has is decided by the backend: this only asks.
 */
export function customerSearch(orgId: string, options: { activeOnly: boolean }): PickerSearch {
  return async (query, signal) => {
    const params = new URLSearchParams({ limit: String(CUSTOMER_CHOICES) });
    if (options.activeOnly) params.set("active", "true");
    if (query !== "") params.set("q", query);

    const result = await apiFetch<Customer[]>(orgId, `/customers?${params.toString()}`, { signal });
    if (!result.ok) return result;
    return {
      ok: true,
      status: result.status,
      data: result.data.map((customer) => ({
        id: customer.id,
        label: customer.name,
        detail: customer.email ?? customer.customer_type,
        inactive: !customer.active,
      })),
    };
  };
}
