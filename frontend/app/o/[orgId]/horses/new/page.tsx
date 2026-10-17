import { NotAllowed } from "@/components/ui/NotAllowed";
import { HorseForm } from "@/features/horses/HorseForm";
import { readActiveRole } from "@/lib/active-role";
import type { Customer } from "@/lib/api/types";
import { canWriteRecords } from "@/lib/roles";
import { serverReadOrNull } from "@/lib/server-api";
import { isUuid } from "@/lib/uuid";

export default async function NewHorsePage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  if (!canWriteRecords(await readActiveRole(orgId))) {
    return <NotAllowed what="horses" back={`/o/${orgId}/horses`} backLabel="Back to horses" />;
  }
  // From a customer's page ("New horse") the owner is already chosen. A made-up or foreign id simply gives an empty owner (it is looked up in this organization only).
  const requested = (await searchParams).owner_customer_id;
  const ownerId = typeof requested === "string" && isUuid(requested) ? requested : null;
  const owner = ownerId === null ? null : await serverReadOrNull<Customer>(orgId, `/api/customers/${ownerId}`);
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-[28px] leading-tight font-semibold tracking-tight">New horse</h1>
      <HorseForm initialOwner={owner !== null && owner.active ? { id: owner.id, label: owner.name } : undefined} />
    </div>
  );
}
