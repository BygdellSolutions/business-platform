import Link from "next/link";

export interface AttentionItem {
  label: string;
  count: number;
  href: string;
}

/** The things that are late or stuck, as one line of links; with nothing to do it says so. Only counts above zero are shown. */
export function AttentionStrip({ items }: { items: AttentionItem[] }) {
  const open = items.filter((item) => item.count > 0);
  if (open.length === 0) {
    return (
      <p className="tint tone-success rounded-lg px-4 py-2.5 text-sm" role="status" data-testid="attention-clear">
        All clear: nothing is late or waiting for you.
      </p>
    );
  }
  return (
    <section aria-label="Needs attention" className="tint tone-warning flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg px-4 py-3" data-testid="attention">
      <h2 className="text-sm font-semibold">Needs attention</h2>
      <ul className="flex flex-wrap gap-2">
        {open.map((item) => (
          <li key={item.label}>
            <Link href={item.href} className="inline-flex items-center gap-2 rounded-full border border-border-strong bg-panel px-3 py-1 text-sm hover:bg-surface-hover">
              <span className="font-semibold tabular-nums">{item.count}</span> {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
