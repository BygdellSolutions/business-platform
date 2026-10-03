import { redirect } from "next/navigation";

import { InvoiceCreateForm } from "@/features/invoices/InvoiceCreateForm";
import type { Invoiceable } from "@/lib/api/types";
import { getIdentity } from "@/lib/identity";
import { backendQuery, pageOf, parseListParams } from "@/lib/list-params";
import { getMemberships } from "@/lib/orgs";
import { canMutateInvoices } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";
import { Notice } from "@/components/ui/Notice";

/**
 * Create a draft invoice from completed transactions. What can be chosen is whatever
 * GET /api/invoiceable-transactions lists for THIS organization (completed, with a currency, on no
 * invoice). The customer filter is the backend's own; paging keeps the selection.
 */
export default async function NewInvoicePage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const list = parseListParams(await searchParams, [], ["customer_id"], {});
  const email = await getIdentity();
  if (email === null) redirect("/dev-login");
  const [rows, memberships] = await Promise.all([
    serverRead<Invoiceable[]>(orgId, "/api/invoiceable-transactions", backendQuery({ ...list, q: "" })),
    getMemberships(email),
  ]);
  const role = memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;
  const { rows: eligible, hasNext } = pageOf(rows);
  const canMutate = canMutateInvoices(role);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New invoice</h1>
      {canMutate ? (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">Choose completed transactions of one billing customer and one currency. The draft reserves them; deleting the draft releases them.</p>
      ) : (
        <Notice testId="read-only">Only owners, admins and accountants can create invoices. You can see what is waiting to be invoiced.</Notice>
      )}
      <InvoiceCreateForm key={orgId} rows={eligible} hasNext={hasNext} base={`/o/${orgId}/invoices/new`} page={list.page} customerFilter={list.refs.customer_id ?? null} canMutate={canMutate} />
    </div>
  );
}
