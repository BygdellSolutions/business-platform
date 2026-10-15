import Link from "next/link";

import { Pagination } from "@/components/ui/Pagination";
import { NewJournalEntry } from "@/features/journal/NewJournalEntry";
import { readActiveRole } from "@/lib/active-role";
import type { Horse, JournalSummary, Organization } from "@/lib/api/types";
import { PAGE_SIZE, pageOf } from "@/lib/list-params";
import { canUseJournal } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";
import { formatTimestamp } from "@/lib/timestamps";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

/** The horse's journal, newest visit first, 25 to a page, with an optional date range. Accountants and viewers have no access. */
export default async function JournalPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, id } = await params;
  const horseId = requireUuid(id);
  const base = `/o/${orgId}/horses/${horseId}`;
  const role = await readActiveRole(orgId);
  if (!canUseJournal(role)) {
    return (
      <div className="flex flex-col gap-2" data-testid="journal-not-allowed">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Journal</h1>
        <p>Your role in this organization has no access to the horse journal.</p>
        <Link href={base} className="text-sm underline">
          Back to the horse
        </Link>
      </div>
    );
  }

  const query = await searchParams;
  const from = first(query.from);
  const to = first(query.to);
  const page = Math.max(1, Number.parseInt(first(query.page) ?? "1", 10) || 1);
  const search = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String((page - 1) * PAGE_SIZE) });
  if (from && DAY.test(from)) search.set("date_from", from);
  if (to && DAY.test(to)) search.set("date_to", to);

  const [horse, organization, rows] = await Promise.all([
    serverRead<Horse>(orgId, `/api/horses/${horseId}`),
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<JournalSummary[]>(orgId, `/api/horses/${horseId}/journal`, `?${search}`),
  ]);
  const { rows: entries, hasNext } = pageOf(rows);
  const hrefFor = (target: number) => {
    const keep = new URLSearchParams();
    if (from && DAY.test(from)) keep.set("from", from);
    if (to && DAY.test(to)) keep.set("to", to);
    if (target > 1) keep.set("page", String(target));
    return `${base}/journal${keep.size > 0 ? `?${keep}` : ""}`;
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Journal: {horse.name}</h1>
        <Link href={base} className="text-sm underline">
          Back to the horse
        </Link>
      </div>
      <NewJournalEntry horseId={horse.id} />

      <form method="get" action={`${base}/journal`} className="panel flex flex-wrap items-end gap-3" aria-label="Filter the journal">
        <label className="flex flex-col gap-1 text-xs font-medium">
          From
          <input type="date" name="from" defaultValue={from && DAY.test(from) ? from : ""} className="border px-2.5 text-sm" />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium">
          To
          <input type="date" name="to" defaultValue={to && DAY.test(to) ? to : ""} className="border px-2.5 text-sm" />
        </label>
        <button type="submit" className="underline">
          Filter
        </button>
      </form>

      <div className="panel flex flex-col gap-3">
        {entries.length === 0 ? (
          <p data-testid="empty">{from || to ? "No entries in that period." : "No journal entries yet."}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-zinc-200 dark:divide-zinc-800" data-testid="journal-list">
            {entries.map((entry) => (
              <li key={entry.id} data-testid="journal-row" className="flex flex-col gap-1 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Link href={`${base}/journal/${entry.id}`} className="font-medium underline">
                    {formatTimestamp(entry.visit_at, organization.timezone)}
                  </Link>
                  <span className={entry.status === "draft" ? "rounded bg-amber-100 px-1.5 text-xs text-amber-900" : "rounded bg-zinc-100 px-1.5 text-xs text-zinc-700"} data-testid="entry-status">
                    {entry.status === "draft" ? "Draft" : "Finalized"}
                  </span>
                  <span className="text-sm text-zinc-600 dark:text-zinc-400">{entry.practitioner_name}</span>
                  {entry.mark_count > 0 && <span className="text-xs text-zinc-500">{entry.mark_count} {entry.mark_count === 1 ? "mark" : "marks"}</span>}
                </div>
                {entry.preview && <p className="text-sm text-zinc-700 dark:text-zinc-300">{entry.preview}</p>}
              </li>
            ))}
          </ul>
        )}
        <Pagination page={page} hasNext={hasNext} hrefFor={hrefFor} />
      </div>
    </div>
  );
}
