import Link from "next/link";

import { CONTROL, FILTER_LABEL } from "@/components/ui/controls";
import { DecimalText } from "@/components/ui/DecimalText";
import { EmptyState } from "@/components/ui/EmptyState";
import { ListFilters } from "@/components/ui/ListFilters";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { SortHeader } from "@/components/ui/SortHeader";
import { CustomerFilter } from "@/features/customers/CustomerFilter";
import { InvoiceStatusBadge, RETURN_OPEN_CLASS } from "@/features/invoices/InvoiceStatusBadge";
import type { Customer, InvoiceFilterOptions, InvoiceSummary } from "@/lib/api/types";
import { requireCredential } from "@/lib/auth/credential";
import { formatShortDate } from "@/lib/dates";
import { backendQuery, listHref, sortHref, pageOf, parseListParams, type ExtraSpec } from "@/lib/list-params";
import { serverRead, serverReadOrNull } from "@/lib/server-api";

const SORTS = ["number", "customer", "invoice_date", "due_date", "status", "returns", "currency", "net", "vat", "gross", "paid", "outstanding", "credited", "refunded"] as const;

/**
 * The filters GET /api/invoices supports. The CHOICES of the four drop-downs (status, sent, payment, returns) are not written here: they are
 * asked of the backend (`/api/invoices/filter-options`), which builds them from the very types the list accepts, so this page can never offer
 * a status the invoices no longer have or miss one they have gained.
 */
const FILTERS = ["state", "sent", "payment", "returns"] as const;
const DATES: Record<string, ExtraSpec> = { date_from: "date", date_to: "date", paid_from: "date", paid_to: "date" };

/** One drop-down of the filter row, its choices as the backend gave them (plus "All"). */
function OptionFilter({ name, label, options, value }: { name: string; label: string; options: { value: string; label: string }[]; value: string | undefined }) {
  return (
    <label className={FILTER_LABEL}>
      {label}
      <select name={name} defaultValue={value ?? ""} className={CONTROL}>
        <option value="">All</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

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
  const options = await serverRead<InvoiceFilterOptions>(orgId, "/api/invoices/filter-options");
  // What the address may carry is what the backend offers (an unknown word in the address is ignored, as before).
  const extras: Record<string, ExtraSpec> = { ...DATES, ...Object.fromEntries(FILTERS.map((name) => [name, options[name].map((option) => option.value)])) };
  const list = parseListParams(raw, [], ["customer_id"], extras, SORTS);
  const customerId = list.refs.customer_id;
  await requireCredential(`/o/${orgId}`);

  const [rows, filterCustomer] = await Promise.all([
    serverRead<InvoiceSummary[]>(orgId, "/api/invoices", backendQuery(list)),
    customerId === undefined ? null : serverReadOrNull<Customer>(orgId, `/api/customers/${customerId}`),
  ]);
  const { rows: invoices, hasNext } = pageOf(rows);
  const base = `/o/${orgId}/invoices`;
  const filtered = list.q !== "" || Object.keys(list.refs).length > 0 || Object.keys(list.extra).length > 0;
  const initialCustomer =
    customerId === undefined ? null : filterCustomer === null ? { id: customerId, label: "Unknown customer" } : { id: customerId, label: filterCustomer.name, inactive: !filterCustomer.active };

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Invoices" description="Issued and draft invoices, payments and credit notes." />
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
      <div className="panel flex flex-col gap-4">
        <ListFilters action={base} params={list} activeStatus={false}>
          <OptionFilter name="state" label="Status" options={options.state} value={list.extra.state} />
          <OptionFilter name="sent" label="Sent" options={options.sent} value={list.extra.sent} />
          <OptionFilter name="payment" label="Payment" options={options.payment} value={list.extra.payment} />
          <OptionFilter name="returns" label="Returns" options={options.returns} value={list.extra.returns} />
          <CustomerFilter key={`customer-${customerId ?? ""}`} name="customer_id" label="Customer" initial={initialCustomer} />
          <label className={FILTER_LABEL}>
            From
            <input type="date" name="date_from" defaultValue={list.extra.date_from ?? ""} className={CONTROL} />
          </label>
          <label className={FILTER_LABEL}>
            To
            <input type="date" name="date_to" defaultValue={list.extra.date_to ?? ""} className={CONTROL} />
          </label>
        </ListFilters>

        {invoices.length === 0 ? (
          <EmptyState
            title={filtered ? "No invoices match." : "No invoices yet."}
            hint={filtered ? "Try a different search or clear the filters." : "Invoices are created from completed orders."}
          />
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
                  <SortHeader label="Return" sortKey="returns" current={list.sort} dir={list.dir} href={sortHref(base, list, "returns")} title="An open return case and its follow-up date" />
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
                      <InvoiceStatusBadge status={invoice.status} paymentStatus={invoice.payment_status} creditStatus={invoice.credit_status} emailed={invoice.emailed} />
                      {invoice.refund_due_amount && invoice.refund_due_amount !== "0.00" && (
                        <span className="ml-2 text-xs text-amber-700 dark:text-amber-300" data-testid="refund-due">
                          Refund due <DecimalText value={invoice.refund_due_amount} />
                        </span>
                      )}
                    </td>
                    <td className="py-1 pr-4" data-testid="invoice-return">
                      {invoice.open_returns === 0 ? (
                        "—"
                      ) : invoice.return_follow_up_due ? (
                        <span className="badge tone-warning">
                          Follow-up due{invoice.return_follow_up_on ? ` ${formatShortDate(invoice.return_follow_up_on)}` : ""}
                        </span>
                      ) : (
                        <span>
                          <span className={RETURN_OPEN_CLASS}>Open</span>
                          {invoice.return_follow_up_on ? ` · follow up ${formatShortDate(invoice.return_follow_up_on)}` : ""}
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
    </div>
  );
}

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}
