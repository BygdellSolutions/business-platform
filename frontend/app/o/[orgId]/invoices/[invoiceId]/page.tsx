import Link from "next/link";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { Notice } from "@/components/ui/Notice";
import { InvoiceDialogs } from "@/features/invoices/InvoiceDialogs";
import { InvoiceView } from "@/features/invoices/InvoiceView";
import type { Invoice, InvoiceEmailDraft, InvoiceEmailLog, Organization } from "@/lib/api/types";
import { requireCredential } from "@/lib/auth/credential";
import { readRecordHistory } from "@/lib/history-server";
import { getMemberships } from "@/lib/orgs";
import { canMutateInvoices } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";
import { formatDay } from "@/lib/timestamps";
import { SECONDARY_LINK } from "@/components/ui/Button";

/**
 * The invoice, read on the server: the single source of truth for the view, which refreshes it
 * after every change. A foreign, random or malformed id ends in the same generic not-found page.
 * Draft and issued share this page; the status (and the user's role) only decide which controls
 * exist. The document itself is rendered from the stored invoice and nothing else: no customer,
 * item or custom-field record is read for it.
 */
export default async function InvoicePage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; invoiceId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, invoiceId } = await params;
  const { created, credit_return: creditReturn } = await searchParams;
  const credential = await requireCredential(`/o/${orgId}`);
  const recordId = requireUuid(invoiceId);
  const [invoice, memberships, organization, history] = await Promise.all([
    serverRead<Invoice>(orgId, `/api/invoices/${recordId}`),
    getMemberships(credential),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "invoice", recordId),
  ]);
  // Only an issued invoice can be emailed; the list of attempts is read for it alone.
  const [emails, emailDraft] =
    invoice.status === "issued"
      ? await Promise.all([serverRead<InvoiceEmailLog[]>(orgId, `/api/invoices/${recordId}/emails`), serverRead<InvoiceEmailDraft>(orgId, `/api/invoices/${recordId}/email-draft`)])
      : [[], null];
  const issuedBy = history.history.people.find((person) => person.id === invoice.issued_by)?.name ?? "not recorded";
  const role = memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight" data-testid="record-name">
          {invoice.number_text !== null ? `Invoice ${invoice.number_text}` : "Draft invoice"} · {invoice.customer_name}
        </h1>
        <Link href={`/o/${orgId}/invoices`} className={SECONDARY_LINK}>
          Back to invoices
        </Link>
      </div>
      {created === "1" && <Notice testId="created">Draft invoice created. Its orders are reserved until you issue or delete it.</Notice>}
      <RecordMeta record={invoice} people={history.history.people} timeZone={organization.timezone} />
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-zinc-500">{invoice.transactions.length === 1 ? "This invoice is for:" : "This invoice is for these orders:"}</span>
        <p className="flex flex-wrap gap-2" data-testid="invoice-orders">
          {invoice.transactions.map((source) => (
            <Link
              key={source.transaction_id}
              href={`/o/${orgId}/transactions/${source.transaction_id}`}
              className="badge tone-primary !px-3 !py-1 !text-sm hover:brightness-95 after:ml-1.5 after:content-['→']"
              data-testid="invoice-order-link"
            >
              {source.transaction_number !== null ? `Order ${source.transaction_number}` : `Order of ${source.transaction_date}`}
            </Link>
          ))}
        </p>
      </div>
      {invoice.issued_at !== null && (
        <p className="text-sm text-zinc-500" data-testid="issued-meta">
          Issued {formatDay(invoice.issued_at, organization.timezone)} by <span data-testid="issued-by">{issuedBy}</span>
        </p>
      )}
      <InvoiceView
        key={invoice.id}
        invoice={invoice}
        canMutate={canMutateInvoices(role)}
        timeZone={organization.timezone}
        actions={
          emailDraft !== null ? (
            <InvoiceDialogs
              invoice={invoice}
              canMutate={canMutateInvoices(role)}
              emailDraft={emailDraft}
              emails={emails}
              today={organization.today}
              timeZone={organization.timezone}
              initialCreditReturn={typeof creditReturn === "string" ? creditReturn : undefined}
            />
          ) : null
        }
      />
      <RecordHistory data={history} entityType="invoice" timeZone={organization.timezone} />
    </div>
  );
}
