import type { ItemAvailability } from "@/lib/api/types";

const BADGES: Record<ItemAvailability["states"][number], { label: string; tone: string }> = {
  out_of_stock: { label: "Out of stock", tone: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
  low_stock: { label: "Low stock", tone: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  backordered: { label: "Backordered", tone: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300" },
  incoming: { label: "Incoming", tone: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300" },
};

/** A product's stock states as the backend decided them; several can show at once ("Low stock", "Incoming"). */
export function StockBadges({ states }: { states: ItemAvailability["states"] }) {
  if (states.length === 0) return <span className="text-xs text-zinc-500">In stock</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {states.map((state) => (
        <span key={state} data-testid={`stock-state-${state}`} className={`rounded px-1.5 py-0.5 text-xs ${BADGES[state].tone}`}>
          {BADGES[state].label}
        </span>
      ))}
    </span>
  );
}
