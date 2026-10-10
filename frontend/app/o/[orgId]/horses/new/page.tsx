import { NotAllowed } from "@/components/ui/NotAllowed";
import { HorseForm } from "@/features/horses/HorseForm";
import { readActiveRole } from "@/lib/active-role";
import { canWriteRecords } from "@/lib/roles";

export default async function NewHorsePage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  if (!canWriteRecords(await readActiveRole(orgId))) {
    return <NotAllowed what="horses" back={`/o/${orgId}/horses`} backLabel="Back to horses" />;
  }
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New horse</h1>
      <HorseForm />
    </div>
  );
}
