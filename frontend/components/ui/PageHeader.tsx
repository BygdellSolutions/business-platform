import type { ReactNode } from "react";

/** The top of a page: its title, one line saying what the page is for, and the page's main action on the right. */
export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-prose text-sm text-secondary">{description}</p>}
      </div>
      {actions}
    </div>
  );
}
