import { CustomerForm } from "@/features/customers/CustomerForm";

export default function NewCustomerPage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New customer</h1>
      <CustomerForm />
    </div>
  );
}
