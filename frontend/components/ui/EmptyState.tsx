import type { ReactNode } from "react";

/** An empty list: says what is missing, what to do about it and offers the action. The title carries the `empty` test id. */
export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-strong px-6 py-10 text-center">
      <p data-testid="empty" className="text-base font-medium">
        {title}
      </p>
      {hint && <p className="max-w-md text-sm text-secondary">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
