import Link from "next/link";

import { Notice } from "@/components/ui/Notice";
import { TransactionEditor } from "@/features/transactions/TransactionEditor";
import { readEntityFields } from "@/lib/custom-fields/server";
import type { Transaction } from "@/lib/api/types";
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
  const transaction = await serverRead<Transaction>(orgId, `/api/transactions/${requireUuid(id)}`);
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
          Transaction · {transaction.transaction_date}
        </h1>
        <Link href={`/o/${orgId}/transactions`} className="text-sm underline">
          Back to transactions
        </Link>
      </div>
      {created === "1" && <Notice testId="created">Transaction created. Add its lines below.</Notice>}
      <TransactionEditor
        key={transaction.id}
        transaction={transaction}
        fields={{
          transaction: { definitions: transactionFields.definitions, values: transactionFields.values[transaction.id] ?? [] },
          line: { definitions: lineFields.definitions, values: lineFields.values },
        }}
      />
    </div>
  );
}
