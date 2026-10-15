import Link from "next/link";

import { Pagination } from "@/components/ui/Pagination";
import { JournalTable } from "@/features/journal/JournalTable";
import { readActiveRole } from "@/lib/active-role";
import type { JournalSummary, Organization } from "@/lib/api/types";
import { backendQuery, listHref, pageOf, parseListParams, sortHref } from "@/lib/list-params";
import { canUseJournal } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";

const SORTS = ["number", "horse", "visit", "by", "marks", "follow_up"] as const;

/**
 * The journal entries that are not finalized yet, of every horse: what the dashboard's "Draft journal entries" counts. An owner or
 * admin sees all of them, an employee their own (the ones they can finish). Accountants and viewers have no journal.
 */
export default async function DraftJournalPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const role = await readActiveRole(orgId);
  const base = `/o/${orgId}`;
  if (!canUseJournal(role)) {
    return (
      <div className="flex flex-col gap-2" data-testid="journal-not-allowed">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Draft journal entries</h1>
        <p>Your role in this organization has no access to the horse journal.</p>
        <Link href={base} className="text-sm underline">
          Back to the dashboard
        </Link>
      </div>
    );
  }

  const list = parseListParams(await searchParams, [], [], {}, SORTS);
  const journalBase = `${base}/journal`;
  const [organization, rows] = await Promise.all([
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<JournalSummary[]>(orgId, "/api/journal/drafts", backendQuery(list)),
  ]);
  const { rows: entries, hasNext } = pageOf(rows);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Draft journal entries</h1>
        <Link href={base} className="text-sm underline">
          Back to the dashboard
        </Link>
      </div>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        {role === "employee" ? "Your entries that are not finalized yet." : "Entries not finalized yet, of every horse."} Open one to finish or delete it.
      </p>
      <div className="panel flex flex-col gap-3">
        {entries.length === 0 ? (
          <p data-testid="empty">No draft journal entries. Everything is finalized.</p>
        ) : (
          <JournalTable
            orgId={orgId}
            entries={entries}
            timeZone={organization.timezone}
            showHorse
            sort={{ current: list.sort, dir: list.dir, hrefs: Object.fromEntries(SORTS.map((key) => [key, sortHref(journalBase, list, key)])) }}
          />
        )}
        <Pagination page={list.page} hasNext={hasNext} hrefFor={(page) => listHref(journalBase, list, { page })} />
      </div>
    </div>
  );
}
