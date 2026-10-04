import Link from "next/link";

import { Notice } from "@/components/ui/Notice";
import { InvoiceView } from "@/features/invoices/InvoiceView";
import type { Invoice } from "@/lib/api/types";
import { requireCredential } from "@/lib/auth/credential";
import { getMemberships } from "@/lib/orgs";
import { canMutateInvoices } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

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
  const { created } = await searchParams;
  const credential = await requireCredential(`/o/${orgId}`);
  const [invoice, memberships] = await Promise.all([serverRead<Invoice>(orgId, `/api/invoices/${requireUuid(invoiceId)}`), getMemberships(credential)]);
  const role = memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold" data-testid="record-name">
          {invoice.number_text !== null ? `Invoice ${invoice.number_text}` : "Draft invoice"} · {invoice.customer_name}
        </h1>
        <Link href={`/o/${orgId}/invoices`} className="text-sm underline">
          Back to invoices
        </Link>
      </div>
      {created === "1" && <Notice testId="created">Draft invoice created. Its transactions are reserved until you issue or delete it.</Notice>}
      <InvoiceView key={invoice.id} invoice={invoice} canMutate={canMutateInvoices(role)} />
    </div>
  );
}
