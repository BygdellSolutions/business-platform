import { redirect } from "next/navigation";

import { EarlierTransactions } from "@/features/settings/EarlierTransactions";
import { OrganizationSettings } from "@/features/settings/OrganizationSettings";
import type { CurrencyStatus, Organization, Role } from "@/lib/api/types";
import { getIdentity } from "@/lib/identity";
import { getMemberships } from "@/lib/orgs";
import { serverRead } from "@/lib/server-api";

/** Who is offered the editing form. Only a convenience: FastAPI refuses the change for any other role. */
const SETTINGS_ROLES: Role[] = ["owner", "admin"];

/** The active organization's settings and currency status, read from FastAPI for this organization only. */
export default async function SettingsPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  const email = await getIdentity();
  if (email === null) redirect("/dev-login");

  const [organization, status, memberships] = await Promise.all([
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<CurrencyStatus>(orgId, "/api/transactions/currency-status"),
    getMemberships(email),
  ]);
  const role = memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;
  const canEdit = role !== undefined && SETTINGS_ROLES.includes(role);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <OrganizationSettings key={organization.id} organization={organization} canEdit={canEdit} />
      <EarlierTransactions status={status} canEdit={canEdit} />
    </div>
  );
}
