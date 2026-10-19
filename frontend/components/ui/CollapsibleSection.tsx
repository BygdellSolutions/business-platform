import type { ReactNode } from "react";

/**
 * A section of a record's page whose HEADING opens and closes it ("▸ Orders (12)"), so long lists take space only
 * when someone wants them. Each section is its own collapsible and starts closed. A plain <details>, so it works
 * without JavaScript and on server pages; the count (when given) shows what is inside while it is closed.
 */
export function CollapsibleSection({
  title,
  count,
  testId,
  toggleTestId,
  className = "max-w-4xl",
  defaultOpen = false,
  bare = false,
  children,
}: {
  title: string;
  count?: number;
  /** On the <section>. */
  testId: string;
  /** On the <details> (whose `open` attribute says whether it is open). */
  toggleTestId: string;
  className?: string;
  /** Open when the page is drawn (the person can still close it). The sections of lists start closed; the main content of a page may start open. */
  defaultOpen?: boolean;
  /** Inside a pop-up window, which has the title and the frame already: just the content (still carrying `testId`). */
  bare?: boolean;
  children: ReactNode;
}) {
  if (bare) return <div data-testid={testId} className="flex flex-col gap-2">{children}</div>;
  return (
    <section aria-label={title} data-testid={testId} className={`panel flex flex-col ${className}`}>
      <details data-testid={toggleTestId} className="group" open={defaultOpen || undefined}>
        <summary className="flex cursor-pointer list-none items-center gap-2 select-none [&::-webkit-details-marker]:hidden">
          <span aria-hidden="true" className="inline-block text-sm text-zinc-500 transition-transform group-open:rotate-90">
            ▶
          </span>
          <h2 className="text-lg font-semibold">
            {title}
            {count !== undefined && <span className="font-normal text-zinc-500"> ({count})</span>}
          </h2>
        </summary>
        <div className="mt-2 flex flex-col gap-2">{children}</div>
      </details>
    </section>
  );
}
