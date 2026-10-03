import { ItemForm } from "@/features/catalog/ItemForm";
import { Notice } from "@/components/ui/Notice";
import type { Item } from "@/lib/api/types";
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
  const item = await serverRead<Item>(orgId, `/api/items/${requireUuid(id)}`);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold" data-testid="record-name">
        {item.name}
      </h1>
      {created === "1" && <Notice testId="created">Item created.</Notice>}
      <ItemForm key={item.id} item={item} />
    </div>
  );
}
