import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { ItemDetails } from "@/features/catalog/ItemDetails";
import { ItemDialogs } from "@/features/catalog/ItemDialogs";
import { ItemForm } from "@/features/catalog/ItemForm";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { Backorder, Incoming, Item, ItemAvailability, ItemDiscount, Organization, Stock } from "@/lib/api/types";
import { readRecordHistory } from "@/lib/history-server";
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
  const { created, stock: stockResult } = await searchParams;
  const recordId = requireUuid(id);
  const [item, role, organization, history, discounts, stock, availability, incoming, backorders] = await Promise.all([
    serverRead<Item>(orgId, `/api/items/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "item", recordId),
    serverRead<ItemDiscount[]>(orgId, `/api/items/${recordId}/discounts`),
    serverRead<Stock>(orgId, `/api/items/${recordId}/stock`),
    serverRead<ItemAvailability[]>(orgId, "/api/inventory/availability", `?${new URLSearchParams({ item_id: recordId })}`),
    serverRead<Incoming[]>(orgId, "/api/inventory/incoming", `?${new URLSearchParams({ item_id: recordId, open_only: "false" })}`),
    serverRead<Backorder[]>(orgId, "/api/inventory/backorders", `?${new URLSearchParams({ item_id: recordId })}`),
  ]);

  const dialogs = (
    <ItemDialogs
      itemId={item.id}
      tracksStock={item.track_stock}
      stock={stock}
      figures={availability[0] ?? null}
      backorders={backorders}
      incoming={incoming}
      discounts={discounts}
      canWriteRecords={canWriteRecords(role)}
      canManageDiscounts={role === "owner" || role === "admin"}
      timeZone={organization.timezone}
    />
  );

  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <h1 className="text-[28px] leading-tight font-semibold tracking-tight" data-testid="record-name">
        {item.name}
      </h1>
      <p className="text-sm text-zinc-500" data-testid="record-number">
        Item no. {item.number}
      </p>
      {created === "1" && <Notice testId="created">Item created.</Notice>}
      {stockResult === "failed" && <Notice testId="stock-failed">The opening stock was not recorded. Record it with the Stock button below.</Notice>}
      <RecordMeta record={item} people={history.history.people} timeZone={organization.timezone} />
      {canWriteRecords(role) ? <ItemForm key={item.id} item={item} actions={dialogs} /> : (
        <>
          <div className="flex flex-wrap gap-2">{dialogs}</div>
          <ItemDetails item={item} />
        </>
      )}
      {item.type === "product" && !item.track_stock && canWriteRecords(role) && (
        <Notice testId="stock-off">This product does not track stock. Tick “Track stock” above and save to record what is on hand.</Notice>
      )}
      <RecordHistory data={history} entityType="item" timeZone={organization.timezone} />
    </div>
  );
}
