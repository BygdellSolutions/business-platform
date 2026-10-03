import Link from "next/link";

import { Notice } from "@/components/ui/Notice";
import { TransactionEditor } from "@/features/transactions/TransactionEditor";
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
      <TransactionEditor key={transaction.id} transaction={transaction} />
    </div>
  );
}
