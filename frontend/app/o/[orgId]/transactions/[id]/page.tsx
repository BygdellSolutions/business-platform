import Link from "next/link";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { Notice } from "@/components/ui/Notice";
import { TransactionEditor } from "@/features/transactions/TransactionEditor";
import { readActiveRole } from "@/lib/active-role";
import { readEntityFields } from "@/lib/custom-fields/server";
import type { InvoiceEmailDraft, InvoiceEmailLog, InvoiceStateOfOrder, LineFulfillment, Organization, StockDemand, Transaction } from "@/lib/api/types";
import { readRecordHistory } from "@/lib/history-server";
import { canMutateInvoices, canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";
import { SECONDARY_LINK } from "@/components/ui/Button";

/**
 * The transaction, read on the server: the single source of truth for the editor, which
 * refreshes it after every change. A foreign, random or malformed id ends in the same generic
 * not-found page. Draft, completed and cancelled share this page; the status only decides what
 * the editor offers.
 */
export default async function TransactionPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, id } = await params;
  const { created } = await searchParams;
  const recordId = requireUuid(id);
  const [transaction, role, organization, history, stock, fulfillment, [invoice]] = await Promise.all([
    serverRead<Transaction>(orgId, `/api/transactions/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "transaction", recordId),
    serverRead<StockDemand[]>(orgId, `/api/inventory/transactions/${recordId}`),
    serverRead<LineFulfillment[]>(orgId, `/api/inventory/transactions/${recordId}/fulfillment`),
    serverRead<InvoiceStateOfOrder[]>(orgId, "/api/invoices/by-transaction", `?${new URLSearchParams({ ids: recordId })}`),
  ]);
  // The organization's custom-field definitions for transactions and for lines, and the values of
  // this transaction and its lines: read here, on the server, like everything else on the page.
  const [transactionFields, lineFields] = await Promise.all([
    readEntityFields(orgId, "transaction", [transaction.id]),
    readEntityFields(
      orgId,
      "transaction_line",
      transaction.lines.map((line) => line.id),
    ),
  ]);

  // An order paid at the counter has a receipt to email: its address, subject and the earlier sends are read here.
  const receiptEmail =
    transaction.paid_at !== null
      ? {
          draft: await serverRead<InvoiceEmailDraft>(orgId, `/api/invoices/receipts/${recordId}/email-draft`),
          emails: await serverRead<InvoiceEmailLog[]>(orgId, `/api/invoices/receipts/${recordId}/emails`),
          canSend: canWriteRecords(role),
          timeZone: organization.timezone,
        }
      : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight" data-testid="record-name">
          Order {transaction.number} · {transaction.transaction_date}
        </h1>
        <Link href={`/o/${orgId}/transactions`} className={SECONDARY_LINK}>
          Back to orders
        </Link>
      </div>
      {created === "1" && <Notice testId="created">Order created. Add its lines below.</Notice>}
      <RecordMeta record={transaction} people={history.history.people} timeZone={organization.timezone} />
      {invoice && invoice.invoices.length > 0 && (
        // Invoicing by delivery: an order can be on several invoices, one per delivery.
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-zinc-500">{invoice.invoices.length === 1 ? "This order is on:" : "This order is on these invoices:"}</span>
          <p className="flex flex-wrap gap-2" data-testid="order-invoice">
            {invoice.invoices.map((entry) => (
              <Link key={entry.invoice_id} href={`/o/${orgId}/invoices/${entry.invoice_id}`} className="badge tone-primary !px-3 !py-1 !text-sm hover:brightness-95 after:ml-1.5 after:content-['→']">
                {entry.status === "issued" ? `Invoice ${entry.number_text ?? ""}` : "Draft invoice"}
              </Link>
            ))}
          </p>
        </div>
      )}
      <TransactionEditor
        key={transaction.id}
        transaction={transaction}
        canEdit={canWriteRecords(role)}
        canInvoice={canMutateInvoices(role)}
        invoiceState={invoice?.state ?? "none"}
        receiptEmail={receiptEmail}
        timeZone={organization.timezone}
        stock={stock}
        fulfillment={fulfillment}
        fields={{
          transaction: { definitions: transactionFields.definitions, values: transactionFields.values[transaction.id] ?? [] },
          line: { definitions: lineFields.definitions, values: lineFields.values },
        }}
      />
      <RecordHistory data={history} entityType="transaction" timeZone={organization.timezone} />
    </div>
  );
}
