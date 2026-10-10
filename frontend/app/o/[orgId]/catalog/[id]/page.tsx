import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { ItemDetails } from "@/features/catalog/ItemDetails";
import { ItemDiscounts } from "@/features/catalog/ItemDiscounts";
import { ItemForm } from "@/features/catalog/ItemForm";
import { BackordersPanel } from "@/features/catalog/BackordersPanel";
import { IncomingPanel } from "@/features/catalog/IncomingPanel";
import { StockPanel } from "@/features/catalog/StockPanel";
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

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold" data-testid="record-name">
        {item.name}
      </h1>
      {created === "1" && <Notice testId="created">Item created.</Notice>}
      {stockResult === "failed" && <Notice testId="stock-failed">The opening stock was not recorded. Record it in the Stock section below.</Notice>}
      <RecordMeta record={item} people={history.history.people} timeZone={organization.timezone} />
      {canWriteRecords(role) ? <ItemForm key={item.id} item={item} /> : <ItemDetails item={item} />}
      {item.type === "product" && !item.track_stock && canWriteRecords(role) && (
        <Notice testId="stock-off">This product does not track stock. Tick “Track stock” above and save to record what is on hand.</Notice>
      )}
      {item.track_stock && (
        <>
          <StockPanel itemId={item.id} unit={item.unit} stock={stock} figures={availability[0] ?? null} canAdjust={canWriteRecords(role)} timeZone={organization.timezone} />
          <BackordersPanel itemId={item.id} unit={item.unit} backorders={backorders} canAllocate={canWriteRecords(role)} />
          <IncomingPanel itemId={item.id} unit={item.unit} incoming={incoming} canWrite={canWriteRecords(role)} timeZone={organization.timezone} />
        </>
      )}
      <ItemDiscounts itemId={item.id} discounts={discounts} canManage={role === "owner" || role === "admin"} />
      <RecordHistory data={history} entityType="item" timeZone={organization.timezone} />
    </div>
  );
}
