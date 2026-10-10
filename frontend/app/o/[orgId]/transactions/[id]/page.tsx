import Link from "next/link";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { Notice } from "@/components/ui/Notice";
import { TransactionEditor } from "@/features/transactions/TransactionEditor";
import { readActiveRole } from "@/lib/active-role";
import { readEntityFields } from "@/lib/custom-fields/server";
import type { LineFulfillment, Organization, StockDemand, Transaction } from "@/lib/api/types";
import { readRecordHistory } from "@/lib/history-server";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

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
  const [transaction, role, organization, history, stock, fulfillment] = await Promise.all([
    serverRead<Transaction>(orgId, `/api/transactions/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "transaction", recordId),
    serverRead<StockDemand[]>(orgId, `/api/inventory/transactions/${recordId}`),
    serverRead<LineFulfillment[]>(orgId, `/api/inventory/transactions/${recordId}/fulfillment`),
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

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold" data-testid="record-name">
          Order {transaction.number} · {transaction.transaction_date}
        </h1>
        <Link href={`/o/${orgId}/transactions`} className="text-sm underline">
          Back to orders
        </Link>
      </div>
      {created === "1" && <Notice testId="created">Order created. Add its lines below.</Notice>}
      <RecordMeta record={transaction} people={history.history.people} timeZone={organization.timezone} />
      <TransactionEditor
        key={transaction.id}
        transaction={transaction}
        canEdit={canWriteRecords(role)}
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
