import type { ItemAvailability } from "@/lib/api/types";

const BADGES: Record<ItemAvailability["states"][number], { label: string; tone: string }> = {
  out_of_stock: { label: "Out of stock", tone: "tone-danger" },
  low_stock: { label: "Low stock", tone: "tone-warning" },
  backordered: { label: "Backordered", tone: "tone-purple" },
  incoming: { label: "Incoming", tone: "tone-info" },
};

/** A product's stock states as the backend decided them; several can show at once ("Low stock", "Incoming"). */
export function StockBadges({ states }: { states: ItemAvailability["states"] }) {
  if (states.length === 0) return <span className="badge tone-success">In stock</span>;
  return (
    // One line, never wrapped: a wrapped "Out of / stock" pair made a table row look like two rows.
    <span className="inline-flex gap-1 whitespace-nowrap">
      {states.map((state) => (
        <span key={state} data-testid={`stock-state-${state}`} className={`badge ${BADGES[state].tone}`}>
          {BADGES[state].label}
        </span>
      ))}
    </span>
  );
}
