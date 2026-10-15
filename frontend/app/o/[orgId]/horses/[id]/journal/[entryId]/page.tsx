import Link from "next/link";

import { JournalEditor } from "@/features/journal/JournalEditor";
import { JournalEntryView } from "@/features/journal/JournalEntryView";
import { readActiveRole } from "@/lib/active-role";
import type { Horse, JournalEntry, Organization } from "@/lib/api/types";
import { canUseJournal } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

/** One journal entry: the editor while it is a draft someone may change, otherwise the frozen entry with its corrections. */
export default async function JournalEntryPage({ params }: { params: Promise<{ orgId: string; id: string; entryId: string }> }) {
  const { orgId, id, entryId } = await params;
  const horseId = requireUuid(id);
  const base = `/o/${orgId}/horses/${horseId}`;
  if (!canUseJournal(await readActiveRole(orgId))) {
    return (
      <div className="flex flex-col gap-2" data-testid="journal-not-allowed">
        <p>Your role in this organization has no access to the horse journal.</p>
        <Link href={base} className="text-sm underline">
          Back to the horse
        </Link>
      </div>
    );
  }
  const [horse, organization, entry] = await Promise.all([
    serverRead<Horse>(orgId, `/api/horses/${horseId}`),
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<JournalEntry>(orgId, `/api/horses/${horseId}/journal/${requireUuid(entryId)}`),
  ]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">
          Journal entry no. {entry.number}: {horse.name}
          {entry.status === "draft" && <span className="ml-2 rounded bg-amber-100 px-2 text-sm font-normal text-amber-900">Draft</span>}
        </h1>
        <Link href={`${base}/journal`} className="text-sm underline">
          All entries
        </Link>
      </div>
      {entry.status === "draft" && entry.can_change ? (
        <JournalEditor key={`${entry.id}-${entry.version}`} entry={entry} />
      ) : (
        <>
          {entry.status === "draft" && (
            <p data-testid="draft-readonly" className="text-sm text-zinc-600">
              This is a draft written by {entry.created_by_name ?? "someone else"}; only they, an admin or an owner can change it.
            </p>
          )}
          <JournalEntryView entry={entry} timeZone={organization.timezone} />
        </>
      )}
    </div>
  );
}
