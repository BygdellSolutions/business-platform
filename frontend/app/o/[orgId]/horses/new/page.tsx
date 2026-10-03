import { HorseForm } from "@/features/horses/HorseForm";

export default function NewHorsePage() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">New horse</h1>
      <HorseForm />
    </div>
  );
}
