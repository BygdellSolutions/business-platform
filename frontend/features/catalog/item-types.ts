import type { ItemType } from "@/lib/api/types";

/** The catalog's item types in display order (a plain module: server pages and client forms both read it). */
export const ITEM_TYPE_LABELS: Record<ItemType, string> = {
  service: "Service",
  product: "Product",
  charge: "Charge (travel, mileage, fees)",
};

export const ITEM_TYPES = Object.keys(ITEM_TYPE_LABELS) as ItemType[];
