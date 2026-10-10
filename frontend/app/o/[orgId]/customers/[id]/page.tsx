import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { ServiceList } from "@/components/history/ServiceList";
import { CustomerBought } from "@/features/customers/CustomerBought";
import { CustomerDetails } from "@/features/customers/CustomerDetails";
import { CustomerForm } from "@/features/customers/CustomerForm";
import { CustomerOrders } from "@/features/customers/CustomerOrders";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { BoughtLine, Customer, InvoiceStateOfOrder, Organization, ServiceRecord, TransactionSummary } from "@/lib/api/types";
import { readRecordHistory } from "@/lib/history-server";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

const ORDERS_SHOWN = 10;
const BOUGHT_SHOWN = 50;

/**
 * A customer id from another organization, a random UUID and a malformed id all end here in
 * the same generic not-found page: FastAPI answers 404 for the first two, and the id shape
 * check covers the third.
 */
export default async function CustomerPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, id } = await params;
  const { created } = await searchParams;
  const recordId = requireUuid(id);
  const [customer, role, organization, history, services, orderRows, bought] = await Promise.all([
    serverRead<Customer>(orgId, `/api/customers/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "customer", recordId),
    serverRead<ServiceRecord[]>(orgId, "/api/transactions/services", `?${new URLSearchParams({ billing_customer_id: recordId })}`),
    serverRead<TransactionSummary[]>(orgId, "/api/transactions", `?${new URLSearchParams({ billing_customer_id: recordId, limit: String(ORDERS_SHOWN + 1) })}`),
    serverRead<BoughtLine[]>(orgId, "/api/transactions/bought", `?${new URLSearchParams({ billing_customer_id: recordId, limit: String(BOUGHT_SHOWN) })}`),
  ]);
  // One more than shown is asked for, to know whether the Orders list has more.
  const orders = orderRows.slice(0, ORDERS_SHOWN);
  const invoices =
    orders.length === 0 ? [] : await serverRead<InvoiceStateOfOrder[]>(orgId, "/api/invoices/by-transaction", `?${new URLSearchParams({ ids: orders.map((order) => order.id).join(",") })}`);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold" data-testid="record-name">
        {customer.name}
      </h1>
      {created === "1" && <Notice testId="created">Customer created.</Notice>}
      <RecordMeta record={customer} people={history.history.people} timeZone={organization.timezone} />
      {canWriteRecords(role) ? <CustomerForm key={customer.id} customer={customer} canSetDiscount={role === "owner" || role === "admin"} /> : <CustomerDetails customer={customer} />}
      <CustomerOrders orgId={orgId} customerId={recordId} orders={orders} invoices={invoices} hasMore={orderRows.length > ORDERS_SHOWN} />
      <CustomerBought orgId={orgId} lines={bought} />
      <ServiceList orgId={orgId} services={services} timeZone={organization.timezone} title="Services billed to this customer" />
      <RecordHistory data={history} entityType="customer" timeZone={organization.timezone} />
    </div>
  );
}
