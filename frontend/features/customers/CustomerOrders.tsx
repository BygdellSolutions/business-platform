"use client";

import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import { OrderCells, OrderHeaders } from "@/components/history/OrderColumns";
import { PAYMENT_METHODS } from "@/features/invoices/payment-labels";
import type { InvoiceStateOfOrder, TransactionSummary } from "@/lib/api/types";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: TransactionSummary) => SortValue> = {
  number: (r) => ({ number: r.number }),
  date: (r) => ({ text: r.transaction_date }),
  status: (r) => ({ text: r.status }),
  lines: (r) => ({ number: r.line_count }),
  total: (r) => ({ decimal: r.totals.gross_amount }),
};

const METHOD_LABELS = Object.fromEntries(PAYMENT_METHODS.map((method) => [method.value, method.label]));

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
  bare = false,
}: {
  orgId: string;
  customerId: string;
  orders: TransactionSummary[];
  invoices: InvoiceStateOfOrder[];
  hasMore: boolean;
  bare?: boolean;
}) {
  const invoiceOf = new Map(invoices.map((state) => [state.transaction_id, state]));
  const allOrders = `/o/${orgId}/transactions?billing_customer_id=${customerId}`;

  const sorted = useSortedRows(orders, SORT_COLUMNS);
  return (
    <CollapsibleSection title={hasMore ? "Latest orders" : "Orders"} count={orders.length} testId="customer-orders" toggleTestId="orders-toggle" bare={bare}>
      {orders.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-orders">
          No orders yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <OrderHeaders header={sorted.header} />
                <th className="py-1 pr-4">Invoice / payment</th>
                <SortHeader label="Lines" {...sorted.header("lines")} align="right" />
                <SortHeader label="Total" {...sorted.header("total")} align="right" last />
              </tr>
            </thead>
            <tbody>
              {sorted.rows.map((order) => {
                const invoice = invoiceOf.get(order.id);
                return (
                  <tr key={order.id} data-testid="customer-order" className="border-b border-zinc-200 dark:border-zinc-800">
                    <OrderCells orgId={orgId} transactionId={order.id} number={order.number} date={order.transaction_date} status={order.status} />
                    <td className="py-1 pr-4" data-testid="customer-order-invoice">
                      {order.paid_at !== null ? (
                        <span data-testid="customer-order-paid">
                          Paid directly{order.payment_method ? ` · ${METHOD_LABELS[order.payment_method] ?? order.payment_method}` : ""}
                          {order.receipt_number_text && ` · receipt ${order.receipt_number_text}`}
                        </span>
                      ) : invoice && invoice.state !== "none" && invoice.invoice_id ? (
                        <Link href={`/o/${orgId}/invoices/${invoice.invoice_id}`} className="underline">
                          {invoice.state === "draft" ? "Draft invoice" : `Invoice ${invoice.number_text ?? ""}`}
                          {invoice.state === "partly" && " (partly; the rest when delivered)"}
                        </Link>
                      ) : invoice?.state === "waiting" ? (
                        <span className="text-zinc-500">Waiting for delivery</span>
                      ) : order.status === "completed" ? (
                        <span className="text-zinc-500">Not invoiced yet</span>
                      ) : (
                        <span className="text-zinc-500">—</span>
                      )}
                    </td>
                    <td className="py-1 pr-4 text-right">{order.line_count}</td>
                    <td className="py-1 text-right">
                      <DecimalText value={order.totals.gross_amount} /> {order.currency ?? ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {hasMore && (
        <Link href={allOrders} className="text-sm underline" data-testid="all-customer-orders">
          All orders of this customer
        </Link>
      )}
    </CollapsibleSection>
  );
}
