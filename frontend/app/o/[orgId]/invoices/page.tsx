import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { ListFilters } from "@/components/ui/ListFilters";
import { Notice } from "@/components/ui/Notice";
import { Pagination } from "@/components/ui/Pagination";
import { SortHeader } from "@/components/ui/SortHeader";
import { CustomerFilter } from "@/features/customers/CustomerFilter";
import { InvoiceStatusBadge } from "@/features/invoices/InvoiceStatusBadge";
import type { Customer, InvoiceSummary } from "@/lib/api/types";
import { requireCredential } from "@/lib/auth/credential";
import { backendQuery, listHref, sortHref, pageOf, parseListParams, type ExtraSpec } from "@/lib/list-params";
import { getMemberships } from "@/lib/orgs";
import { canMutateInvoices } from "@/lib/roles";
import { serverRead, serverReadOrNull } from "@/lib/server-api";

const SORTS = ["number", "customer", "invoice_date", "due_date", "status", "currency", "net", "vat", "gross", "paid", "outstanding", "credited", "refunded"] as const;

const STATUSES = ["draft", "issued"] as const;
// Exactly the filters GET /api/invoices supports: status, customer, invoice date range, search (customer name or number).
const PAYMENTS = ["open", "overdue", "not_yet_due", "unpaid", "partially_paid", "paid", "refund_due"] as const;
const CREDITS = ["any", "partly_credited", "credited"] as const;
const RETURNS = ["open", "follow_up_due"] as const;
const EXTRAS: Record<string, ExtraSpec> = { status: STATUSES, payment: PAYMENTS, credit: CREDITS, returns: RETURNS, date_from: "date", date_to: "date", paid_from: "date", paid_to: "date" };
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
  const list = parseListParams(raw, [], ["customer_id"], EXTRAS, SORTS);
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
      {first(raw.deleted) === "1" && <Notice testId="deleted">Draft deleted. Its orders can be invoiced again.</Notice>}

      {(list.extra.paid_from || list.extra.paid_to) && (
        <Notice testId="paid-period">
          Invoices with a payment {list.extra.paid_from ? `from ${list.extra.paid_from} ` : ""}
          {list.extra.paid_to ? `to ${list.extra.paid_to}` : ""}.{" "}
          <Link href={base} className="underline">
            Show all
          </Link>
        </Notice>
      )}
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
            <option value="overdue">Overdue</option>
            <option value="not_yet_due">Not yet due</option>
            <option value="unpaid">Unpaid</option>
            <option value="partially_paid">Partially paid</option>
            <option value="paid">Paid</option>
            <option value="refund_due">Refund due</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Credit notes
          <select name="credit" defaultValue={list.extra.credit ?? ""} className={CONTROL}>
            <option value="">All</option>
            <option value="any">Credited (all or part)</option>
            <option value="partly_credited">Partly credited</option>
            <option value="credited">Fully credited</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Returns
          <select name="returns" defaultValue={list.extra.returns ?? ""} className={CONTROL}>
            <option value="">All</option>
            <option value="open">Return open</option>
            <option value="follow_up_due">Follow-up due</option>
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
          <table data-testid="invoices-table" className="w-full max-w-6xl text-left text-sm whitespace-nowrap">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <SortHeader label="Number" sortKey="number" current={list.sort} dir={list.dir} href={sortHref(base, list, "number")} />
                <SortHeader label="Customer" sortKey="customer" current={list.sort} dir={list.dir} href={sortHref(base, list, "customer")} />
                <SortHeader label="Invoice date" sortKey="invoice_date" current={list.sort} dir={list.dir} href={sortHref(base, list, "invoice_date")} />
                <SortHeader label="Due date" sortKey="due_date" current={list.sort} dir={list.dir} href={sortHref(base, list, "due_date")} />
                <SortHeader label="Status" sortKey="status" current={list.sort} dir={list.dir} href={sortHref(base, list, "status")} />
                <SortHeader label="Currency" sortKey="currency" current={list.sort} dir={list.dir} href={sortHref(base, list, "currency")} />
                <SortHeader label="Net" sortKey="net" current={list.sort} dir={list.dir} href={sortHref(base, list, "net")} align="right" />
                <SortHeader label="VAT" sortKey="vat" current={list.sort} dir={list.dir} href={sortHref(base, list, "vat")} align="right" />
                <SortHeader label="Gross" sortKey="gross" current={list.sort} dir={list.dir} href={sortHref(base, list, "gross")} align="right" />
                <SortHeader label="Paid" sortKey="paid" current={list.sort} dir={list.dir} href={sortHref(base, list, "paid")} align="right" title="Payments received, less refunds" />
                <SortHeader label="Remaining" sortKey="outstanding" current={list.sort} dir={list.dir} href={sortHref(base, list, "outstanding")} align="right" title="Still to be paid, after credit notes" />
                <SortHeader label="Credited" sortKey="credited" current={list.sort} dir={list.dir} href={sortHref(base, list, "credited")} align="right" title="Credit notes, incl. VAT" />
                <SortHeader label="Refunded" sortKey="refunded" current={list.sort} dir={list.dir} href={sortHref(base, list, "refunded")} align="right" title="Money paid back to the customer" last />
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
                    <InvoiceStatusBadge status={invoice.status} paymentStatus={invoice.payment_status} creditStatus={invoice.credit_status} openReturns={invoice.open_returns} />
                    {invoice.refund_due_amount && invoice.refund_due_amount !== "0.00" && (
                      <span className="ml-2 text-xs text-amber-700 dark:text-amber-300" data-testid="refund-due">
                        Refund due <DecimalText value={invoice.refund_due_amount} />
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
                  <td className="py-1 pr-4 text-right" data-testid="invoice-gross">
                    <DecimalText value={invoice.gross_amount} />
                  </td>
                  {/* Issued invoices only (a draft cannot be paid or credited); nothing credited or refunded shows "—". */}
                  <td className="py-1 pr-4 text-right" data-testid="invoice-paid">
                    {invoice.paid_amount !== null && <DecimalText value={invoice.paid_amount} />}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="invoice-outstanding">
                    {invoice.outstanding_amount !== null && <DecimalText value={invoice.outstanding_amount} />}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="invoice-credited">
                    {invoice.credited_amount !== null && (invoice.credited_amount === "0.00" ? "—" : <DecimalText value={invoice.credited_amount} />)}
                  </td>
                  <td className="py-1 text-right" data-testid="invoice-refunded">
                    {invoice.refunded_amount !== null && (invoice.refunded_amount === "0.00" ? "—" : <DecimalText value={invoice.refunded_amount} />)}
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
