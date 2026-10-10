import { NotAllowed } from "@/components/ui/NotAllowed";
import { SupplierForm } from "@/features/suppliers/SupplierForm";
import { readActiveRole } from "@/lib/active-role";
import { canWriteRecords } from "@/lib/roles";

export default async function NewSupplierPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  if (!canWriteRecords(await readActiveRole(orgId))) {
    return <NotAllowed what="suppliers" back={`/o/${orgId}/suppliers`} backLabel="Back to suppliers" />;
  }
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New supplier</h1>
      <SupplierForm />
    </div>
  );
}
