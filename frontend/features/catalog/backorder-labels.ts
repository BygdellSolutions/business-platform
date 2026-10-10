import type { Backorder } from "@/lib/api/types";

/** A backorder's state as people read it. A plain module (not a client component), so server pages can use it too. */
export const BACKORDER_STATES: Record<Backorder["state"], string> = {
  waiting_for_stock: "Waiting for stock",
  partially_fulfilled: "Partially fulfilled",
  ready_to_fulfill: "Ready to fulfill",
  fulfilled: "Fulfilled",
  cancelled: "Cancelled",
};
