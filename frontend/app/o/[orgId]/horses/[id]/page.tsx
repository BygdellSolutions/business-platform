import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { HorseDetails } from "@/features/horses/HorseDetails";
import { HorseForm } from "@/features/horses/HorseForm";
import { HorseDialogs } from "@/features/horses/HorseDialogs";
import { SHOWN_ON_HORSE_PAGE } from "@/features/journal/HorseJournalSection";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { Horse, HorseDocument, HorseNote, JournalSummary, Organization, ServiceRecord } from "@/lib/api/types";
import { readRecordHistory } from "@/lib/history-server";
import { canUseJournal, canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

/** A foreign, random or malformed horse id all end in the same generic not-found page. */
export default async function HorsePage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, id } = await params;
  const { created } = await searchParams;
  const recordId = requireUuid(id);
  const role = await readActiveRole(orgId);
  const [horse, organization, history, services, notes, documents, journal] = await Promise.all([
    serverRead<Horse>(orgId, `/api/horses/${recordId}`),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "horse", recordId),
    serverRead<ServiceRecord[]>(orgId, "/api/transactions/services", `?${new URLSearchParams({ subject_type: "horse", subject_id: recordId })}`),
    serverRead<HorseNote[]>(orgId, `/api/horses/${recordId}/notes`),
    serverRead<HorseDocument[]>(orgId, `/api/horses/${recordId}/documents`),
    // Accountants and viewers have no journal at all: nothing is even asked for.
    canUseJournal(role) ? serverRead<JournalSummary[]>(orgId, `/api/horses/${recordId}/journal`, `?limit=${SHOWN_ON_HORSE_PAGE + 1}`) : Promise.resolve(null),
  ]);

  const dialogs = (
    <HorseDialogs
      orgId={orgId}
      horseId={horse.id}
      journal={journal === null ? null : journal.slice(0, SHOWN_ON_HORSE_PAGE)}
      journalHasMore={journal !== null && journal.length > SHOWN_ON_HORSE_PAGE}
      documents={documents}
      notes={notes}
      services={services}
      canWrite={canWriteRecords(role)}
      timeZone={organization.timezone}
    />
  );

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-[28px] leading-tight font-semibold tracking-tight" data-testid="record-name">
        {horse.name}
      </h1>
      <p className="text-sm text-zinc-500" data-testid="record-number">
        Horse no. {horse.number}
      </p>
      {created === "1" && <Notice testId="created">Horse created.</Notice>}
      <RecordMeta record={horse} people={history.history.people} timeZone={organization.timezone} />
      {canWriteRecords(role) ? (
        <HorseForm key={horse.id} horse={horse} actions={dialogs} />
      ) : (
        <>
          <div className="flex flex-wrap gap-2">{dialogs}</div>
          <HorseDetails orgId={orgId} horse={horse} />
        </>
      )}
      <RecordHistory data={history} entityType="horse" timeZone={organization.timezone} />
    </div>
  );
}
