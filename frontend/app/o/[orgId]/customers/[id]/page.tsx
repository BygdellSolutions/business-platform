import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { CustomerDialogs } from "@/features/customers/CustomerDialogs";
import { CustomerDetails } from "@/features/customers/CustomerDetails";
import { CustomerForm } from "@/features/customers/CustomerForm";
import { HORSES_SHOWN, horsesOfCustomer } from "@/features/customers/CustomerHorses";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { BoughtLine, Customer, Horse, InvoiceStateOfOrder, Organization, ServiceRecord, TransactionSummary } from "@/lib/api/types";
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
  const [customer, role, organization, history, services, orderRows, bought, owned, stabled] = await Promise.all([
    serverRead<Customer>(orgId, `/api/customers/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "customer", recordId),
    serverRead<ServiceRecord[]>(orgId, "/api/transactions/services", `?${new URLSearchParams({ billing_customer_id: recordId })}`),
    serverRead<TransactionSummary[]>(orgId, "/api/transactions", `?${new URLSearchParams({ billing_customer_id: recordId, limit: String(ORDERS_SHOWN + 1) })}`),
    serverRead<BoughtLine[]>(orgId, "/api/transactions/bought", `?${new URLSearchParams({ billing_customer_id: recordId, limit: String(BOUGHT_SHOWN) })}`),
    // One more than shown is asked for, to know whether there are more horses than the section lists.
    serverRead<Horse[]>(orgId, "/api/horses", `?${new URLSearchParams({ owner_customer_id: recordId, limit: String(HORSES_SHOWN + 1) })}`),
    serverRead<Horse[]>(orgId, "/api/horses", `?${new URLSearchParams({ stable_customer_id: recordId, limit: String(HORSES_SHOWN + 1) })}`),
  ]);
  // One more than shown is asked for, to know whether the Orders list has more.
  const orders = orderRows.slice(0, ORDERS_SHOWN);
  const invoices =
    orders.length === 0 ? [] : await serverRead<InvoiceStateOfOrder[]>(orgId, "/api/invoices/by-transaction", `?${new URLSearchParams({ ids: orders.map((order) => order.id).join(",") })}`);

  const dialogs = (
    <CustomerDialogs
      orgId={orgId}
      customerId={recordId}
      owned={owned}
      stabled={stabled}
      horseCount={horsesOfCustomer(owned.slice(0, HORSES_SHOWN), stabled.slice(0, HORSES_SHOWN)).length}
      orders={orders}
      ordersHaveMore={orderRows.length > ORDERS_SHOWN}
      invoices={invoices}
      bought={bought}
      services={services}
      canWrite={canWriteRecords(role)}
      timeZone={organization.timezone}
    />
  );

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-[28px] leading-tight font-semibold tracking-tight" data-testid="record-name">
        {customer.name}
      </h1>
      <p className="text-sm text-zinc-500" data-testid="record-number">
        Customer no. {customer.number}
      </p>
      {created === "1" && <Notice testId="created">Customer created.</Notice>}
      <RecordMeta record={customer} people={history.history.people} timeZone={organization.timezone} />
      <section aria-label="Customer information" data-testid="customer-info" className="flex max-w-3xl flex-col gap-2">
        <h2 className="text-lg font-semibold">Customer information</h2>
        {canWriteRecords(role) ? (
          <CustomerForm key={customer.id} customer={customer} canSetDiscount={role === "owner" || role === "admin"} actions={dialogs} />
        ) : (
          <>
            <div className="flex flex-wrap gap-2">{dialogs}</div>
            <CustomerDetails customer={customer} />
          </>
        )}
      </section>
      <RecordHistory data={history} entityType="customer" timeZone={organization.timezone} />
    </div>
  );
}
