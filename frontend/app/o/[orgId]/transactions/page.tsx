import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { CustomerFilter } from "@/features/customers/CustomerFilter";
import { TransactionStatusBadge } from "@/features/transactions/TransactionStatusBadge";
import { readActiveRole } from "@/lib/active-role";
import type { Customer, TransactionSummary } from "@/lib/api/types";
import { backendQuery, listHref, pageOf, parseListParams, type ExtraSpec } from "@/lib/list-params";
import { canWriteRecords } from "@/lib/roles";
import { serverRead, serverReadOrNull } from "@/lib/server-api";

const STATUSES = ["draft", "completed", "cancelled"] as const;
const EXTRAS: Record<string, ExtraSpec> = { status: STATUSES, date_from: "date", date_to: "date" };
const CONTROL = "rounded border border-zinc-400 px-2 py-1 text-sm dark:bg-zinc-900";

/**
 * Transactions with their totals as FastAPI calculated them (sums of the stored line amounts).
 * Filters live in the address; the customer filter also finds customers that were deactivated
 * since. Newest first, as the server orders them.
 */
export default async function TransactionsPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const canWrite = canWriteRecords(await readActiveRole(orgId));
  const list = parseListParams(await searchParams, [], ["billing_customer_id"], EXTRAS);
  const customerId = list.refs.billing_customer_id;
  const [rows, filterCustomer] = await Promise.all([
    serverRead<TransactionSummary[]>(orgId, "/api/transactions", backendQuery(list)),
    customerId === undefined ? null : serverReadOrNull<Customer>(orgId, `/api/customers/${customerId}`),
  ]);
  const { rows: transactions, hasNext } = pageOf(rows);
  const base = `/o/${orgId}/transactions`;
  const filtered = Object.keys(list.refs).length > 0 || Object.keys(list.extra).length > 0;
  const initialCustomer =
    customerId === undefined ? null : filterCustomer === null ? { id: customerId, label: "Unknown customer" } : { id: customerId, label: filterCustomer.name, inactive: !filterCustomer.active };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Orders</h1>
        {canWrite && (
          <Link href={`${base}/new`} className="underline" data-testid="new-transaction">
            New order
          </Link>
        )}
      </div>

      <ListFilters action={base} params={list} search={false} activeStatus={false}>
        <label className="flex flex-col gap-1 text-sm">
          Status
          <select name="status" defaultValue={list.extra.status ?? ""} className={CONTROL}>
            <option value="">All</option>
            <option value="draft">Draft</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
        <CustomerFilter key={`customer-${customerId ?? ""}`} name="billing_customer_id" label="Customer" initial={initialCustomer} />
        <label className="flex flex-col gap-1 text-sm">
          From
          <input type="date" name="date_from" defaultValue={list.extra.date_from ?? ""} className={CONTROL} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          To
          <input type="date" name="date_to" defaultValue={list.extra.date_to ?? ""} className={CONTROL} />
        </label>
      </ListFilters>

      {transactions.length === 0 ? (
        <p data-testid="empty">
          {filtered ? "No orders match." : "No orders yet."}{" "}
          {!filtered && canWrite && (
            <Link href={`${base}/new`} className="underline">
              Create the first one.
            </Link>
          )}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table data-testid="transactions-table" className="w-full max-w-5xl text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Date</th>
                <th className="py-1 pr-4">Customer</th>
                <th className="py-1 pr-4">Status</th>
                <th className="py-1 pr-4">Currency</th>
                <th className="py-1 pr-4 text-right">Lines</th>
                <th className="py-1 pr-4 text-right">Net</th>
                <th className="py-1 pr-4 text-right">VAT</th>
                <th className="py-1 text-right">Gross</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((transaction) => (
                <tr key={transaction.id} data-testid="transaction-row" data-status={transaction.status} className={`border-b border-zinc-200 dark:border-zinc-800 ${transaction.status === "cancelled" ? "text-zinc-500" : ""}`}>
                  <td className="py-1 pr-4">
                    <Link href={`${base}/${transaction.id}`} className="underline" data-testid="transaction-link">
                      {transaction.transaction_date}
                    </Link>
                  </td>
                  <td className="py-1 pr-4" data-testid="transaction-customer">
                    <Link href={`/o/${orgId}/customers/${transaction.billing_customer_id}`} className="underline">
                      {transaction.billing_customer.name}
                    </Link>
                    {!transaction.billing_customer.active && <span className="ml-1 text-xs text-zinc-500">(inactive)</span>}
                  </td>
                  <td className="py-1 pr-4">
                    <TransactionStatusBadge status={transaction.status} />
                  </td>
                  <td className="py-1 pr-4" data-testid="transaction-currency">
                    {transaction.currency ?? <span className="text-zinc-500">none</span>}
                  </td>
                  <td className="py-1 pr-4 text-right">{transaction.line_count}</td>
                  <td className="py-1 pr-4 text-right" data-testid="transaction-net">
                    <DecimalText value={transaction.totals.net_amount} />
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="transaction-vat">
                    <DecimalText value={transaction.totals.vat_amount} />
                  </td>
                  <td className="py-1 text-right" data-testid="transaction-gross">
                    <DecimalText value={transaction.totals.gross_amount} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination page={list.page} hasNext={hasNext} hrefFor={(page) => listHref(base, list, { page })} />
    </div>
  );
}
