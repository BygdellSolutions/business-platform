export function StatusBadge({ active }: { active: boolean }) {
  return (
    <span
      data-testid="status"
      className={`rounded px-2 py-0.5 text-xs ${active ? "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200" : "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"}`}
    >
      {active ? "Active" : "Inactive"}
    </span>
  );
}
