import { NotAllowed } from "@/components/ui/NotAllowed";
import { CustomerForm } from "@/features/customers/CustomerForm";
import { readActiveRole } from "@/lib/active-role";
import { canWriteRecords } from "@/lib/roles";

export default async function NewCustomerPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  const role = await readActiveRole(orgId);
  if (!canWriteRecords(role)) {
    return <NotAllowed what="customers" back={`/o/${orgId}/customers`} backLabel="Back to customers" />;
  }
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New customer</h1>
      <CustomerForm canSetDiscount={role === "owner" || role === "admin"} />
    </div>
  );
}
