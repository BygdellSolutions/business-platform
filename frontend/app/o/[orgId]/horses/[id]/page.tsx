import { HorseDetails } from "@/features/horses/HorseDetails";
import { HorseForm } from "@/features/horses/HorseForm";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { Horse } from "@/lib/api/types";
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
  const [horse, role] = await Promise.all([serverRead<Horse>(orgId, `/api/horses/${requireUuid(id)}`), readActiveRole(orgId)]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold" data-testid="record-name">
        {horse.name}
      </h1>
      {created === "1" && <Notice testId="created">Horse created.</Notice>}
      {canWriteRecords(role) ? <HorseForm key={horse.id} horse={horse} /> : <HorseDetails orgId={orgId} horse={horse} />}
    </div>
  );
}
