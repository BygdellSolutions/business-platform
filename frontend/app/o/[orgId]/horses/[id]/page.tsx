import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { ServiceList } from "@/components/history/ServiceList";
import { HorseDetails } from "@/features/horses/HorseDetails";
import { HorseForm } from "@/features/horses/HorseForm";
import { HorseNotes } from "@/features/horses/HorseNotes";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { Horse, HorseNote, Organization, ServiceRecord } from "@/lib/api/types";
import { readRecordHistory } from "@/lib/history-server";
import { canWriteRecords } from "@/lib/roles";
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
  const [horse, role, organization, history, services, notes] = await Promise.all([
    serverRead<Horse>(orgId, `/api/horses/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "horse", recordId),
    serverRead<ServiceRecord[]>(orgId, "/api/transactions/services", `?${new URLSearchParams({ subject_type: "horse", subject_id: recordId })}`),
    serverRead<HorseNote[]>(orgId, `/api/horses/${recordId}/notes`),
  ]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold" data-testid="record-name">
        {horse.name}
      </h1>
      <p className="text-sm text-zinc-500" data-testid="record-number">
        Horse no. {horse.number}
      </p>
      {created === "1" && <Notice testId="created">Horse created.</Notice>}
      <RecordMeta record={horse} people={history.history.people} timeZone={organization.timezone} />
      {canWriteRecords(role) ? <HorseForm key={horse.id} horse={horse} /> : <HorseDetails orgId={orgId} horse={horse} />}
      <HorseNotes horseId={horse.id} notes={notes} canWrite={canWriteRecords(role)} timeZone={organization.timezone} />
      <ServiceList orgId={orgId} services={services} timeZone={organization.timezone} title="Services performed on this horse" />
      <RecordHistory data={history} entityType="horse" timeZone={organization.timezone} />
    </div>
  );
}
