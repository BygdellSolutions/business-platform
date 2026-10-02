import { CustomerPreview } from "@/features/dashboard/CustomerPreview";
import { getIdentity } from "@/lib/identity";
import { getMemberships } from "@/lib/orgs";

export default async function Dashboard({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  // The layout has already validated the user and the organization; this read is memoized.
  const email = await getIdentity();
  const result = email ? await getMemberships(email) : null;
  const organization = result?.status === "ok" ? result.memberships.find((m) => m.id === orgId) : undefined;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <p>
        You are working in <strong data-testid="dashboard-org">{organization?.name}</strong> as{" "}
        <strong>{organization?.role}</strong>.
      </p>
      <CustomerPreview />
    </div>
  );
}
