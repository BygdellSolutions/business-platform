import { CustomerDetails } from "@/features/customers/CustomerDetails";
import { CustomerForm } from "@/features/customers/CustomerForm";
import { Notice } from "@/components/ui/Notice";
import { readActiveRole } from "@/lib/active-role";
import type { Customer } from "@/lib/api/types";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

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
  const [customer, role] = await Promise.all([serverRead<Customer>(orgId, `/api/customers/${requireUuid(id)}`), readActiveRole(orgId)]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold" data-testid="record-name">
        {customer.name}
      </h1>
      {created === "1" && <Notice testId="created">Customer created.</Notice>}
      {canWriteRecords(role) ? <CustomerForm key={customer.id} customer={customer} /> : <CustomerDetails customer={customer} />}
    </div>
  );
}
