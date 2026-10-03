import type { PickerSearch } from "@/components/ui/EntityPicker";
import { apiFetch } from "@/lib/api/client";
import type { Item } from "@/lib/api/types";

/** How many choices one search shows; typing narrows it. */
export const ITEM_CHOICES = 20;

/**
 * Searches the ACTIVE catalog items of `orgId` through the BFF, for adding a line. The unit and
 * price are shown in the list only as a hint for choosing: the picker's value is the item's id,
 * and nothing shown here is ever copied into a request. FastAPI snapshots the item itself.
 */
export function itemSearch(orgId: string): PickerSearch {
  return async (query, signal) => {
    const params = new URLSearchParams({ active: "true", limit: String(ITEM_CHOICES) });
    if (query !== "") params.set("q", query);

    const result = await apiFetch<Item[]>(orgId, `/items?${params.toString()}`, { signal });
    if (!result.ok) return result;
    return {
      ok: true,
      status: result.status,
      data: result.data.map((item) => ({ id: item.id, label: item.name, detail: `${item.unit} · ${item.price_ex_vat}`, inactive: !item.active })),
    };
  };
}
