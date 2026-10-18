"use client";

import { useState } from "react";

import { Button, ButtonIcon, ICONS } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { BackordersPanel } from "@/features/catalog/BackordersPanel";
import { IncomingPanel } from "@/features/catalog/IncomingPanel";
import { ItemDiscounts } from "@/features/catalog/ItemDiscounts";
import { StockPanel } from "@/features/catalog/StockPanel";
import type { Backorder, Incoming, ItemAvailability, ItemDiscount, Stock } from "@/lib/api/types";

type Window = "stock" | "backorders" | "incoming" | "discounts";

/**
 * What is done to a catalog item besides editing it, as buttons that open pop-up windows (the same way as on an invoice): "Stock" (on hand,
 * the change form and the movements), "Backorders" (only while some wait for stock), "Incoming" (deliveries on their way) and "Campaign
 * discounts". The windows hold the very same panels as before. Stock, Backorders and Incoming exist only for an item that tracks stock.
 * A link ending in `#backorders` (from the inventory page) opens the Backorders window.
 */
export function ItemDialogs({
  itemId,
  tracksStock,
  stock,
  figures,
  backorders,
  incoming,
  discounts,
  canWriteRecords,
  canManageDiscounts,
  timeZone,
}: {
  itemId: string;
  tracksStock: boolean;
  stock: Stock;
  figures: ItemAvailability | null;
  backorders: Backorder[];
  incoming: Incoming[];
  discounts: ItemDiscount[];
  canWriteRecords: boolean;
  canManageDiscounts: boolean;
  timeZone: string | null;
}) {
  const [open, setOpen] = useState<Window | null>(() =>
    tracksStock && typeof window !== "undefined" && window.location.hash === "#backorders" ? "backorders" : null,
  );
  const close = () => setOpen(null);
  const onTheWay = incoming.filter((delivery) => delivery.state === "expected" || delivery.state === "partially_received").length;
  return (
    <>
      <div className="flex flex-wrap gap-2" data-testid="item-actions">
        {tracksStock && (
          <Button type="button" variant="tonal" onClick={() => setOpen("stock")} data-testid="open-stock">
            <ButtonIcon path={ICONS.stock} />
            Stock
          </Button>
        )}
        {tracksStock && backorders.length > 0 && (
          <Button type="button" variant="tonal" onClick={() => setOpen("backorders")} data-testid="open-backorders">
            <ButtonIcon path={ICONS.returns} />
            {`Backorders (${backorders.length})`}
          </Button>
        )}
        {tracksStock && (
          <Button type="button" variant="tonal" onClick={() => setOpen("incoming")} data-testid="open-incoming">
            <ButtonIcon path={ICONS.incoming} />
            {onTheWay > 0 ? `Incoming (${onTheWay})` : "Incoming"}
          </Button>
        )}
        <Button type="button" variant="tonal" onClick={() => setOpen("discounts")} data-testid="open-discounts">
          <ButtonIcon path={ICONS.discount} />
          {discounts.length > 0 ? `Campaign discounts (${discounts.length})` : "Campaign discounts"}
        </Button>
      </div>
      {tracksStock && (
        <>
          <Modal open={open === "stock"} onClose={close} title="Stock" testId="stock-dialog">
            <StockPanel itemId={itemId} stock={stock} figures={figures} canAdjust={canWriteRecords} timeZone={timeZone} bare />
          </Modal>
          <Modal open={open === "backorders"} onClose={close} title="Backorders" testId="backorders-dialog">
            <BackordersPanel itemId={itemId} backorders={backorders} canAllocate={canWriteRecords} bare />
          </Modal>
          <Modal open={open === "incoming"} onClose={close} title="Incoming" testId="incoming-dialog">
            <IncomingPanel itemId={itemId} incoming={incoming} canWrite={canWriteRecords} timeZone={timeZone} bare />
          </Modal>
        </>
      )}
      <Modal open={open === "discounts"} onClose={close} title="Campaign discounts" testId="discounts-dialog">
        <ItemDiscounts itemId={itemId} discounts={discounts} canManage={canManageDiscounts} bare />
      </Modal>
    </>
  );
}
