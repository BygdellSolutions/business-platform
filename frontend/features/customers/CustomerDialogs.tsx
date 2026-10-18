"use client";

import { useState } from "react";

import { ServiceList } from "@/components/history/ServiceList";
import { Button, ButtonIcon, ICONS } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { CustomerBought } from "@/features/customers/CustomerBought";
import { CustomerHorses } from "@/features/customers/CustomerHorses";
import { CustomerOrders } from "@/features/customers/CustomerOrders";
import type { BoughtLine, Horse, InvoiceStateOfOrder, ServiceRecord, TransactionSummary } from "@/lib/api/types";

type Window = "horses" | "orders" | "bought" | "services";

/**
 * What belongs to a customer besides its own information, as tinted buttons (beside "Deactivate customer") that open pop-up windows, the
 * way an invoice, a catalog item and a horse do: the horses, the orders, the products and other lines bought and the services billed, each
 * with how many there are. The windows hold the very same sections as before.
 */
export function CustomerDialogs({
  orgId,
  customerId,
  owned,
  stabled,
  horseCount,
  orders,
  ordersHaveMore,
  invoices,
  bought,
  services,
  canWrite,
  timeZone,
}: {
  orgId: string;
  customerId: string;
  owned: Horse[];
  stabled: Horse[];
  /** How many horses the Horses window lists (an owned horse that also stands at their stable counts once). */
  horseCount: number;
  orders: TransactionSummary[];
  ordersHaveMore: boolean;
  invoices: InvoiceStateOfOrder[];
  bought: BoughtLine[];
  services: ServiceRecord[];
  canWrite: boolean;
  timeZone: string | null;
}) {
  const [open, setOpen] = useState<Window | null>(null);
  const close = () => setOpen(null);
  return (
    <>
      <Button type="button" variant="tonal" onClick={() => setOpen("horses")} data-testid="open-horses">
        <ButtonIcon path={ICONS.horse} />
        {`Horses (${horseCount})`}
      </Button>
      <Button type="button" variant="tonal" onClick={() => setOpen("orders")} data-testid="open-orders">
        <ButtonIcon path={ICONS.invoice} />
        {`${ordersHaveMore ? "Latest orders" : "Orders"} (${orders.length})`}
      </Button>
      <Button type="button" variant="tonal" onClick={() => setOpen("bought")} data-testid="open-bought">
        <ButtonIcon path={ICONS.stock} />
        {`Products bought (${bought.length})`}
      </Button>
      <Button type="button" variant="tonal" onClick={() => setOpen("services")} data-testid="open-services">
        <ButtonIcon path={ICONS.service} />
        {`Services billed (${services.length})`}
      </Button>
      <Modal open={open === "horses"} onClose={close} title="Horses" testId="horses-dialog" wide>
        <CustomerHorses orgId={orgId} customerId={customerId} owned={owned} stabled={stabled} canWrite={canWrite} bare />
      </Modal>
      <Modal open={open === "orders"} onClose={close} title={ordersHaveMore ? "Latest orders" : "Orders"} testId="orders-dialog" wide>
        <CustomerOrders orgId={orgId} customerId={customerId} orders={orders} invoices={invoices} hasMore={ordersHaveMore} bare />
      </Modal>
      <Modal open={open === "bought"} onClose={close} title="Products and other lines bought" testId="bought-dialog" wide>
        <CustomerBought orgId={orgId} lines={bought} bare />
      </Modal>
      <Modal open={open === "services"} onClose={close} title="Services billed to this customer" testId="services-dialog" wide>
        <ServiceList orgId={orgId} services={services} timeZone={timeZone} title="Services billed to this customer" bare />
      </Modal>
    </>
  );
}
