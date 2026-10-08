
import { DangerZone } from "@/features/settings/DangerZone";
import { EarlierTransactions } from "@/features/settings/EarlierTransactions";
import { OrganizationSettings } from "@/features/settings/OrganizationSettings";
import type { CurrencyStatus, Member, Organization, Role } from "@/lib/api/types";
import { authMode } from "@/lib/auth/config";
import { requireCredential } from "@/lib/auth/credential";
import { getMemberships } from "@/lib/orgs";
import { serverRead } from "@/lib/server-api";

/** Who is offered the editing form. Only a convenience: FastAPI refuses the change for any other role. */
const SETTINGS_ROLES: Role[] = ["owner", "admin"];

/** The active organization's settings and currency status, read from FastAPI for this organization only. */
export default async function SettingsPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  const credential = await requireCredential(`/o/${orgId}`);

  const [organization, status, memberships] = await Promise.all([
    serverRead<Organization>(orgId, "/api/organization"),
    serverRead<CurrencyStatus>(orgId, "/api/transactions/currency-status"),
    getMemberships(credential),
  ]);
  const role = memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;
  const canEdit = role !== undefined && SETTINGS_ROLES.includes(role);
  // Owners and admins may read the member list (for the ownership transfer and the sole-owner rule).
  const members = canEdit ? await serverRead<Member[]>(orgId, "/api/members") : [];

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <OrganizationSettings key={organization.id} organization={organization} canEdit={canEdit} />
      <EarlierTransactions status={status} canEdit={canEdit} />
      <DangerZone organizationName={organization.name} role={role} members={members} passwordChecked={authMode() === "session"} />
    </div>
  );
}
