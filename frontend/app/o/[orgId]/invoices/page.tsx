import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { ListFilters } from "@/components/ui/ListFilters";
import { Notice } from "@/components/ui/Notice";
import { Pagination } from "@/components/ui/Pagination";
import { CustomerFilter } from "@/features/customers/CustomerFilter";
import { InvoiceStatusBadge } from "@/features/invoices/InvoiceStatusBadge";
import { PAYMENT_STATES } from "@/features/invoices/payment-labels";
import type { Customer, InvoiceSummary } from "@/lib/api/types";
import { requireCredential } from "@/lib/auth/credential";
import { backendQuery, listHref, pageOf, parseListParams, type ExtraSpec } from "@/lib/list-params";
import { getMemberships } from "@/lib/orgs";
import { canMutateInvoices } from "@/lib/roles";
import { serverRead, serverReadOrNull } from "@/lib/server-api";

const STATUSES = ["draft", "issued"] as const;
// Exactly the filters GET /api/invoices supports: status, customer, invoice date range, search (customer name or number).
const PAYMENTS = ["open", "unpaid", "partially_paid", "paid"] as const;
const EXTRAS: Record<string, ExtraSpec> = { status: STATUSES, payment: PAYMENTS, date_from: "date", date_to: "date" };
const CONTROL = "rounded border border-zinc-400 px-2 py-1 text-sm dark:bg-zinc-900";

/**
 * Invoices as FastAPI lists them. Every word and amount shown is the invoice's own stored content
 * (the customer NAME is the invoice's snapshot, not the live customer's), and amounts are the
 * server's strings. Filters live in the address and are the backend's: nothing is filtered or
 * sorted in the browser, so a page is never a partial view that was filtered after the fact.
 */
export default async function InvoicesPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const raw = await searchParams;
  const list = parseListParams(raw, [], ["customer_id"], EXTRAS);
  const customerId = list.refs.customer_id;
  const credential = await requireCredential(`/o/${orgId}`);

  const [rows, filterCustomer, memberships] = await Promise.all([
    serverRead<InvoiceSummary[]>(orgId, "/api/invoices", backendQuery(list)),
    customerId === undefined ? null : serverReadOrNull<Customer>(orgId, `/api/customers/${customerId}`),
    getMemberships(credential),
  ]);
  const role = memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;
  const { rows: invoices, hasNext } = pageOf(rows);
  const base = `/o/${orgId}/invoices`;
  const filtered = list.q !== "" || Object.keys(list.refs).length > 0 || Object.keys(list.extra).length > 0;
  const initialCustomer =
    customerId === undefined ? null : filterCustomer === null ? { id: customerId, label: "Unknown customer" } : { id: customerId, label: filterCustomer.name, inactive: !filterCustomer.active };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Invoices</h1>
        {canMutateInvoices(role) && (
          <Link href={`${base}/new`} className="underline" data-testid="new-invoice">
            New invoice
          </Link>
        )}
      </div>
      {first(raw.deleted) === "1" && <Notice testId="deleted">Draft deleted. Its transactions can be invoiced again.</Notice>}

      <ListFilters action={base} params={list} activeStatus={false}>
        <label className="flex flex-col gap-1 text-sm">
          Status
          <select name="status" defaultValue={list.extra.status ?? ""} className={CONTROL}>
            <option value="">All</option>
            <option value="draft">Draft</option>
            <option value="issued">Issued</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Payment
          <select name="payment" defaultValue={list.extra.payment ?? ""} className={CONTROL}>
            <option value="">All</option>
            <option value="open">Not fully paid</option>
            <option value="unpaid">Unpaid</option>
            <option value="partially_paid">Partially paid</option>
            <option value="paid">Paid</option>
          </select>
        </label>
        <CustomerFilter key={`customer-${customerId ?? ""}`} name="customer_id" label="Customer" initial={initialCustomer} />
        <label className="flex flex-col gap-1 text-sm">
          From
          <input type="date" name="date_from" defaultValue={list.extra.date_from ?? ""} className={CONTROL} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          To
          <input type="date" name="date_to" defaultValue={list.extra.date_to ?? ""} className={CONTROL} />
        </label>
      </ListFilters>

      {invoices.length === 0 ? (
        <p data-testid="empty">{filtered ? "No invoices match." : "No invoices yet."}</p>
      ) : (
        <div className="overflow-x-auto">
          <table data-testid="invoices-table" className="w-full max-w-6xl text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Number</th>
                <th className="py-1 pr-4">Customer</th>
                <th className="py-1 pr-4">Invoice date</th>
                <th className="py-1 pr-4">Due date</th>
                <th className="py-1 pr-4">Status</th>
                <th className="py-1 pr-4">Payment</th>
                <th className="py-1 pr-4">Currency</th>
                <th className="py-1 pr-4 text-right">Net</th>
                <th className="py-1 pr-4 text-right">VAT</th>
                <th className="py-1 text-right">Gross</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((invoice) => (
                <tr key={invoice.id} data-testid="invoice-row" data-status={invoice.status} className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">
                    <Link href={`${base}/${invoice.id}`} className="underline" data-testid="invoice-link">
                      <span data-testid="invoice-number">{invoice.number_text ?? "Draft"}</span>
                    </Link>
                  </td>
                  <td className="py-1 pr-4" data-testid="invoice-customer">
                    {invoice.customer_name}
                  </td>
                  <td className="py-1 pr-4" data-testid="invoice-date">
                    {invoice.invoice_date}
                  </td>
                  <td className="py-1 pr-4" data-testid="invoice-due">
                    {invoice.due_date ?? "—"}
                  </td>
                  <td className="py-1 pr-4">
                    <InvoiceStatusBadge status={invoice.status} />
                  </td>
                  <td className="py-1 pr-4" data-testid="invoice-payment">
                    {invoice.payment_status ? PAYMENT_STATES[invoice.payment_status] : ""}
                    {invoice.payment_status === "partially_paid" && invoice.outstanding_amount && (
                      <span className="block text-xs text-zinc-500">
                        <DecimalText value={invoice.outstanding_amount} /> left
                      </span>
                    )}
                  </td>
                  <td className="py-1 pr-4" data-testid="invoice-currency">
                    {invoice.currency}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="invoice-net">
                    <DecimalText value={invoice.net_amount} />
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="invoice-vat">
                    <DecimalText value={invoice.vat_amount} />
                  </td>
                  <td className="py-1 text-right" data-testid="invoice-gross">
                    <DecimalText value={invoice.gross_amount} />
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

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}
