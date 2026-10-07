import { ItemDetails } from "@/features/catalog/ItemDetails";
import { ItemForm } from "@/features/catalog/ItemForm";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { Item } from "@/lib/api/types";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

/** Same not-found behavior as customers: foreign, random and malformed ids are indistinguishable. */
export default async function ItemPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, id } = await params;
  const { created } = await searchParams;
  const [item, role] = await Promise.all([serverRead<Item>(orgId, `/api/items/${requireUuid(id)}`), readActiveRole(orgId)]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold" data-testid="record-name">
        {item.name}
      </h1>
      {created === "1" && <Notice testId="created">Item created.</Notice>}
      {canWriteRecords(role) ? <ItemForm key={item.id} item={item} /> : <ItemDetails item={item} />}
    </div>
  );
}
