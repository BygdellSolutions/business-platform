import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import type { InvoiceStateOfOrder, TransactionStatus, TransactionSummary } from "@/lib/api/types";

const STATUS: Record<TransactionStatus, string> = { draft: "Draft", completed: "Completed", cancelled: "Cancelled" };

/**
 * The orders billed to a customer, newest first (the most recent `shown`; the Orders list has all of them): date,
 * status, number of lines, total, and whether the order is invoiced. Amounts are the backend's strings.
 */
export function CustomerOrders({
  orgId,
  customerId,
  orders,
  invoices,
  hasMore,
}: {
  orgId: string;
  customerId: string;
  orders: TransactionSummary[];
  invoices: InvoiceStateOfOrder[];
  hasMore: boolean;
}) {
  const invoiceOf = new Map(invoices.map((state) => [state.transaction_id, state]));
  const allOrders = `/o/${orgId}/transactions?billing_customer_id=${customerId}`;

  return (
    <section aria-label="Orders" data-testid="customer-orders" className="flex max-w-4xl flex-col gap-2">
      <h2 className="text-lg font-semibold">Orders</h2>
      {orders.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-orders">
          No orders yet.
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Date</th>
              <th className="py-1 pr-4">Status</th>
              <th className="py-1 pr-4 text-right">Lines</th>
              <th className="py-1 pr-4 text-right">Total</th>
              <th className="py-1">Invoice</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const invoice = invoiceOf.get(order.id);
              return (
                <tr key={order.id} data-testid="customer-order" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">
                    <Link href={`/o/${orgId}/transactions/${order.id}`} className="underline">
                      {order.transaction_date}
                    </Link>
                  </td>
                  <td className="py-1 pr-4">{STATUS[order.status]}</td>
                  <td className="py-1 pr-4 text-right">{order.line_count}</td>
                  <td className="py-1 pr-4 text-right">
                    <DecimalText value={order.totals.gross_amount} /> {order.currency ?? ""}
                  </td>
                  <td className="py-1" data-testid="customer-order-invoice">
                    {invoice && invoice.state !== "none" && invoice.invoice_id ? (
                      <Link href={`/o/${orgId}/invoices/${invoice.invoice_id}`} className="underline">
                        {invoice.state === "invoiced" ? `Invoice ${invoice.number_text ?? ""}` : "Draft invoice"}
                      </Link>
                    ) : order.status === "completed" ? (
                      <span className="text-zinc-500">Not invoiced yet</span>
                    ) : (
                      <span className="text-zinc-500">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {hasMore && (
        <Link href={allOrders} className="text-sm underline" data-testid="all-customer-orders">
          All orders of this customer
        </Link>
      )}
    </section>
  );
}
