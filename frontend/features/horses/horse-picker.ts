import type { PickerSearch } from "@/components/ui/EntityPicker";
import { apiFetch } from "@/lib/api/client";
import type { Horse } from "@/lib/api/types";

const HORSE_CHOICES = 20;

/** Searches the ACTIVE horses of `orgId` through the BFF (the backend decides which may be chosen). */
export function horseSearch(orgId: string): PickerSearch {
  return async (query, signal) => {
    const params = new URLSearchParams({ active: "true", limit: String(HORSE_CHOICES) });
    if (query !== "") params.set("q", query);
    const result = await apiFetch<Horse[]>(orgId, `/horses?${params.toString()}`, { signal });
    if (!result.ok) return result;
    return { ok: true, status: result.status, data: result.data.map((horse) => ({ id: horse.id, label: horse.name, detail: horse.owner.name, inactive: !horse.active })) };
  };
}
