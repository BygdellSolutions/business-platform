import { TransactionCreateForm } from "@/features/transactions/TransactionCreateForm";

export default function NewTransactionPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New transaction</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">Choose who is billed and the date. You add the lines on the next page.</p>
      <TransactionCreateForm />
    </div>
  );
}
