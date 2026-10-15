import Link from "next/link";

import { CONTROL, FILTER_LABEL } from "@/components/ui/controls";
import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { JournalTable } from "@/features/journal/JournalTable";
import { NewJournalEntry } from "@/features/journal/NewJournalEntry";
import { readActiveRole } from "@/lib/active-role";
import type { Horse, JournalSummary, Organization } from "@/lib/api/types";
import { backendQuery, listHref, pageOf, parseListParams, sortHref } from "@/lib/list-params";
import { canUseJournal } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

const SORTS = ["number", "visit", "by", "status", "marks"] as const;

/** The horse's journal as a table like the other lists: sortable columns, a date range, 25 to a page. Accountants and viewers have no access. */
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

  const list = parseListParams(await searchParams, [], [], { date_from: "date", date_to: "date" }, SORTS);
  const journalBase = `${base}/journal`;
  const [horse, organization, rows] = await Promise.all([
    serverRead<Horse>(orgId, `/api/horses/${horseId}`),
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<JournalSummary[]>(orgId, `/api/horses/${horseId}/journal`, backendQuery(list)),
  ]);
  const { rows: entries, hasNext } = pageOf(rows);
  const filtered = Object.keys(list.extra).length > 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Journal: {horse.name}</h1>
        <Link href={base} className="text-sm underline">
          Back to the horse
        </Link>
      </div>
      <NewJournalEntry horseId={horse.id} />

      <div className="panel flex flex-col gap-4">
        <ListFilters action={journalBase} params={list} search={false} activeStatus={false}>
          <label className={FILTER_LABEL}>
            From
            <input type="date" name="date_from" defaultValue={list.extra.date_from ?? ""} className={CONTROL} />
          </label>
          <label className={FILTER_LABEL}>
            To
            <input type="date" name="date_to" defaultValue={list.extra.date_to ?? ""} className={CONTROL} />
          </label>
        </ListFilters>

        {entries.length === 0 ? (
          <p data-testid="empty">{filtered ? "No entries in that period." : "No journal entries yet."}</p>
        ) : (
          <JournalTable
            orgId={orgId}
            horseId={horseId}
            entries={entries}
            timeZone={organization.timezone}
            sort={{ current: list.sort, dir: list.dir, hrefs: Object.fromEntries(SORTS.map((key) => [key, sortHref(journalBase, list, key)])) }}
          />
        )}
        <Pagination page={list.page} hasNext={hasNext} hrefFor={(page) => listHref(journalBase, list, { page })} />
      </div>
    </div>
  );
}
