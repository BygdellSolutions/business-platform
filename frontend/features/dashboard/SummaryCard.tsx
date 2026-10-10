import Link from "next/link";
import type { ReactNode } from "react";

import { DecimalText } from "@/components/ui/DecimalText";
import type { CurrencyAmount } from "@/lib/api/types";

/** One figure on the dashboard: a count, its amounts per currency (never added across currencies) and where to act. */
export function SummaryCard({
  title,
  count,
  amounts = [],
  href,
  note,
  testId,
  tone = "plain",
}: {
  title: string;
  count: number;
  amounts?: CurrencyAmount[];
  href?: string;
  note?: ReactNode;
  testId: string;
  tone?: "plain" | "attention";
}) {
  const body = (
    <div
      data-testid={testId}
      className={`flex h-full flex-col gap-1 rounded border p-4 ${
        tone === "attention" && count > 0 ? "border-amber-400 bg-amber-50 dark:border-amber-700 dark:bg-amber-950" : "border-zinc-300 dark:border-zinc-700"
      }`}
    >
      <span className="text-sm text-zinc-600 dark:text-zinc-400">{title}</span>
      <span className="text-2xl font-semibold tabular-nums" data-testid={`${testId}-count`}>
        {count}
      </span>
      {amounts.map((entry) => (
        <span key={entry.currency} className="text-sm tabular-nums" data-testid={`${testId}-amount`}>
          <DecimalText value={entry.amount} /> {entry.currency}
        </span>
      ))}
      {note && <span className="text-xs text-zinc-500">{note}</span>}
    </div>
  );
  return href ? (
    <Link href={href} className="block hover:opacity-90">
      {body}
    </Link>
  ) : (
    body
  );
}
