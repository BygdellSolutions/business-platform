import Link from "next/link";

import { SummaryCard } from "@/features/dashboard/SummaryCard";
import type { InventorySummary, InvoicingSummary, SalesSummary } from "@/lib/api/types";
import { getCredential } from "@/lib/auth/credential";
import { getMemberships } from "@/lib/orgs";
import { canMutateInvoices, canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";

/**
 * The organization at a glance: what needs doing (drafts, sales ready to invoice, invoices past their due date, stock
 * problems) and how the month is going. Each figure comes from the module that owns it and counts this organization
 * only; amounts are per currency. Every card links to the list where the work is done.
 */
export default async function Dashboard({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  // The layout has already validated the user and the organization; this read is memoized.
  const credential = await getCredential();
  const result = credential ? await getMemberships(credential) : null;
  const organization = result?.status === "ok" ? result.memberships.find((m) => m.id === orgId) : undefined;
  const [sales, invoicing, inventory] = await Promise.all([
    serverRead<SalesSummary>(orgId, "/api/transactions/summary"),
    serverRead<InvoicingSummary>(orgId, "/api/invoices/summary"),
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

      <section aria-label="This month" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">This month (from {sales.month_start})</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard
            title="Completed sales"
            count={sales.completed_this_month.count}
            amounts={sales.completed_this_month.amounts}
            href={`${base}/transactions?status=completed&date_from=${sales.month_start}`}
            note="Including VAT."
            testId="card-completed"
          />
          <SummaryCard title="Services performed" count={sales.services_this_month} testId="card-services" />
          <SummaryCard
            title="Invoiced"
            count={invoicing.issued_this_month.count}
            amounts={invoicing.issued_this_month.amounts}
            href={`${base}/invoices?status=issued&date_from=${sales.month_start}`}
            note="Issued invoices, including VAT."
            testId="card-invoiced"
          />
          <SummaryCard
            title="Paid"
            count={invoicing.paid_this_month.count}
            amounts={invoicing.paid_this_month.amounts}
            href={`${base}/invoices?payment=paid`}
            note="Payments recorded with a payment date this month."
            testId="card-paid"
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
