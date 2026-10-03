import { ItemForm } from "@/features/catalog/ItemForm";

export default function NewItemPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New item</h1>
      <ItemForm />
    </div>
  );
}
