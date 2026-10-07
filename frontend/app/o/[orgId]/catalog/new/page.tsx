import { NotAllowed } from "@/components/ui/NotAllowed";
import { ItemForm } from "@/features/catalog/ItemForm";
import { readActiveRole } from "@/lib/active-role";
import { canWriteRecords } from "@/lib/roles";

export default async function NewItemPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  if (!canWriteRecords(await readActiveRole(orgId))) {
    return <NotAllowed what="catalog items" back={`/o/${orgId}/catalog`} backLabel="Back to the catalog" />;
  }
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New item</h1>
      <ItemForm />
    </div>
  );
}
