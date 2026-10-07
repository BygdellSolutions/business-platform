import { NotAllowed } from "@/components/ui/NotAllowed";
import { TransactionCreateForm } from "@/features/transactions/TransactionCreateForm";
import { readActiveRole } from "@/lib/active-role";
import { canWriteRecords } from "@/lib/roles";

export default async function NewTransactionPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  if (!canWriteRecords(await readActiveRole(orgId))) {
    return <NotAllowed what="transactions" back={`/o/${orgId}/transactions`} backLabel="Back to transactions" />;
  }
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New transaction</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">Choose who is billed and the date. You add the lines on the next page.</p>
      <TransactionCreateForm />
    </div>
  );
}
