import Link from "next/link";

import { SortHeader } from "@/components/ui/SortHeader";
import type { TransactionStatus } from "@/lib/api/types";
import type { SortDir } from "@/lib/list-params";

/**
 * The columns the customer's three tables (orders, products and other lines bought, services billed) have in common, in the same
 * place in every one: Order no., Date, Status, and the amount last. One component draws them so they cannot drift apart again.
 */
export const ORDER_STATUS_LABELS: Record<TransactionStatus, string> = { draft: "Draft", completed: "Completed", cancelled: "Cancelled" };

type Header = { sortKey: string; current: string; dir: SortDir; onSort: () => void };

/** The three headings every one of the tables starts with. `header(key)` is what useSortedRows gives; the keys are number, date and status. */
export function OrderHeaders({ header }: { header: (key: string) => Header }) {
  return (
    <>
      <SortHeader label="Order no." {...header("number")} align="right" />
      <SortHeader label="Date" {...header("date")} />
      <SortHeader label="Status" {...header("status")} />
    </>
  );
}

/** The three cells that go under those headings: the order's number (a link to the order), the date, and the order's status. */
export function OrderCells({ orgId, transactionId, number, date, status }: { orgId: string; transactionId: string; number: number; date: string; status: TransactionStatus }) {
  return (
    <>
      <td className="py-1 pr-4 text-right" data-testid="order-number">
        <Link href={`/o/${orgId}/transactions/${transactionId}`} className="underline">
          {number}
        </Link>
      </td>
      <td className="py-1 pr-4 whitespace-nowrap" data-testid="order-date">
        {date}
      </td>
      <td className="py-1 pr-4" data-testid="order-status">
        {ORDER_STATUS_LABELS[status]}
      </td>
    </>
  );
}
