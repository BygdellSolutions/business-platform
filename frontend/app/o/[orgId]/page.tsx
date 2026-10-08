import Link from "next/link";

import { SummaryCard } from "@/features/dashboard/SummaryCard";
import type { InventorySummary, InvoicingSummary, Organization, SalesSummary } from "@/lib/api/types";
import { getCredential } from "@/lib/auth/credential";
import { getMemberships } from "@/lib/orgs";
import { canMutateInvoices, canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";

/**
 * The organization at a glance: what needs doing (drafts, sales ready to invoice, invoices past their due date, stock
 * problems) and how the month is going. Each figure comes from the module that owns it and counts this organization
 * only; amounts are per currency. Every card links to the list where the work is done.
 */
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

export default async function Dashboard({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const requested = (await searchParams).month;
  // Only a well-formed month up to the organization's current one is passed on (the backend refuses a future month);
  // anything else shows the current month.
  const { today } = await serverRead<Organization>(orgId, "/api/organization");
  const month = typeof requested === "string" && MONTH.test(requested) && requested <= today.slice(0, 7) ? requested : null;
  const monthQuery = month ? `?${new URLSearchParams({ month })}` : "";
  // The layout has already validated the user and the organization; this read is memoized.
  const credential = await getCredential();
  const result = credential ? await getMemberships(credential) : null;
  const organization = result?.status === "ok" ? result.memberships.find((m) => m.id === orgId) : undefined;
  const [sales, invoicing, inventory] = await Promise.all([
    serverRead<SalesSummary>(orgId, "/api/transactions/summary", monthQuery),
    serverRead<InvoicingSummary>(orgId, "/api/invoices/summary", monthQuery),
    serverRead<InventorySummary>(orgId, "/api/inventory/summary"),
  ]);
  const base = `/o/${orgId}`;
  const role = organization?.role;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            You are working in <strong data-testid="dashboard-org">{organization?.name}</strong> as <strong>{role}</strong> · {sales.today}
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-sm" data-testid="quick-actions">
          {canWriteRecords(role) && (
            <>
              <Link href={`${base}/transactions/new`} className="rounded border border-zinc-400 px-3 py-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                New transaction
              </Link>
              <Link href={`${base}/customers/new`} className="rounded border border-zinc-400 px-3 py-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                New customer
              </Link>
            </>
          )}
          {canMutateInvoices(role) && (
            <Link href={`${base}/invoices/new`} className="rounded border border-zinc-400 px-3 py-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
              New invoice
            </Link>
          )}
        </div>
      </div>

      <section aria-label="To do" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">To do</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard title="Draft transactions" count={sales.drafts} href={`${base}/transactions?status=draft`} testId="card-drafts" />
          <SummaryCard
            title="Ready to invoice"
            count={invoicing.ready_to_invoice.count}
            amounts={invoicing.ready_to_invoice.amounts}
            href={`${base}/invoices/new`}
            testId="card-ready"
            tone="attention"
          />
          <SummaryCard title="Draft invoices" count={invoicing.draft_invoices} href={`${base}/invoices?status=draft`} testId="card-draft-invoices" />
          <SummaryCard
            title="Past due date"
            count={invoicing.past_due.count}
            amounts={invoicing.past_due.amounts}
            href={`${base}/invoices?payment=open`}
            note="Issued, past the due date and not fully paid: what is still outstanding."
            testId="card-past-due"
            tone="attention"
          />
        </div>
      </section>

      <section aria-label="Pending" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Pending</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard
            title="Unpaid invoices"
            count={invoicing.unpaid.count}
            amounts={invoicing.unpaid.amounts}
            href={`${base}/invoices?payment=open`}
            note="Issued and not fully paid: what is still outstanding."
            testId="card-unpaid"
          />
          <SummaryCard
            title="Not yet due"
            count={invoicing.not_yet_due.count}
            amounts={invoicing.not_yet_due.amounts}
            href={`${base}/invoices?payment=open`}
            note="Outstanding, due date not passed (or none)."
            testId="card-not-yet-due"
          />
          <SummaryCard
            title="Partially paid"
            count={invoicing.partially_paid.count}
            amounts={invoicing.partially_paid.amounts}
            href={`${base}/invoices?payment=partially_paid`}
            note="Some paid; the amount is what is left."
            testId="card-partially-paid"
          />
          <SummaryCard
            title="Ready to invoice"
            count={invoicing.ready_to_invoice.count}
            amounts={invoicing.ready_to_invoice.amounts}
            href={`${base}/invoices/new`}
            note="Completed sales on no invoice yet."
            testId="card-pending-ready"
          />
        </div>
      </section>

      <section aria-label="Month" className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold" data-testid="month-heading">
            {sales.next_month === null ? "This month" : "Month"} · {sales.month} ({sales.month_start} – {sales.month_end})
          </h2>
          <form action={base} className="flex items-center gap-2 text-sm" data-testid="month-picker">
            <Link href={`${base}?month=${sales.previous_month}`} className="rounded border border-zinc-400 px-2 py-1" aria-label="Previous month" data-testid="previous-month">
              ←
            </Link>
            <input type="month" name="month" defaultValue={sales.month} max={sales.today.slice(0, 7)} aria-label="Month" className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
            <button type="submit" className="rounded border border-zinc-400 px-2 py-1">
              Show
            </button>
            {sales.next_month !== null && (
              <>
                <Link href={`${base}?month=${sales.next_month}`} className="rounded border border-zinc-400 px-2 py-1" aria-label="Next month" data-testid="next-month">
                  →
                </Link>
                <Link href={base} className="underline">
                  This month
                </Link>
              </>
            )}
          </form>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard
            title="Completed sales"
            count={sales.completed_this_month.count}
            amounts={sales.completed_this_month.amounts}
            href={`${base}/transactions?status=completed&date_from=${sales.month_start}&date_to=${sales.month_end}`}
            note="Including VAT."
            testId="card-completed"
          />
          <SummaryCard title="Services performed" count={sales.services_this_month} testId="card-services" />
          <SummaryCard
            title="Invoiced"
            count={invoicing.issued_this_month.count}
            amounts={invoicing.issued_this_month.amounts}
            href={`${base}/invoices?status=issued&date_from=${sales.month_start}&date_to=${sales.month_end}`}
            note="Issued invoices, including VAT."
            testId="card-invoiced"
          />
          <SummaryCard
            title="Paid"
            count={invoicing.paid_this_month.count}
            amounts={invoicing.paid_this_month.amounts}
            href={`${base}/invoices?payment=paid`}
            note="Payments with a payment date in this month."
            testId="card-paid"
          />
        </div>
      </section>

      <section aria-label="Year" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold" data-testid="year-heading">
          {sales.year_end === sales.today ? "This year" : "Year"} · {sales.year} ({sales.year_start} – {sales.year_end})
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard
            title="Completed sales"
            count={sales.completed_this_year.count}
            amounts={sales.completed_this_year.amounts}
            href={`${base}/transactions?status=completed&date_from=${sales.year_start}&date_to=${sales.year_end}`}
            note="Including VAT."
            testId="card-year-completed"
          />
          <SummaryCard title="Services performed" count={sales.services_this_year} testId="card-year-services" />
          <SummaryCard
            title="Invoiced"
            count={invoicing.issued_this_year.count}
            amounts={invoicing.issued_this_year.amounts}
            href={`${base}/invoices?status=issued&date_from=${sales.year_start}&date_to=${sales.year_end}`}
            note="Issued invoices, including VAT."
            testId="card-year-invoiced"
          />
          <SummaryCard
            title="Paid"
            count={invoicing.paid_this_year.count}
            amounts={invoicing.paid_this_year.amounts}
            href={`${base}/invoices?payment=paid`}
            note="Payments with a payment date in this year."
            testId="card-year-paid"
          />
        </div>
      </section>

      {inventory.tracked_items > 0 && (
        <section aria-label="Stock" className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold">Stock</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <SummaryCard title="Out of stock" count={inventory.out_of_stock} href={`${base}/catalog?type=product`} testId="card-out-of-stock" tone="attention" />
            <SummaryCard title="Low stock" count={inventory.low_stock} href={`${base}/catalog?type=product`} testId="card-low-stock" tone="attention" />
            <SummaryCard
              title="Sales waiting for stock"
              count={inventory.open_backorders}
              note={inventory.backordered_items > 0 ? `${inventory.backordered_items} product(s)` : undefined}
              href={`${base}/inventory`}
              testId="card-backorders"
              tone="attention"
            />
            <SummaryCard title="Deliveries on their way" count={inventory.incoming_deliveries} href={`${base}/inventory`} testId="card-incoming" />
          </div>
        </section>
      )}

    </div>
  );
}
