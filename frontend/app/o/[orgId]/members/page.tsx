import { Notice } from "@/components/ui/Notice";
import { InvitationHistory } from "@/features/members/InvitationHistory";
import { InvitationsAdmin } from "@/features/members/InvitationsAdmin";
import { MembersAdmin } from "@/features/members/MembersAdmin";
import type { Invitation, Member, Organization } from "@/lib/api/types";
import { requireCredential } from "@/lib/auth/credential";
import { canAdminister } from "@/lib/members";
import { getMemberships } from "@/lib/orgs";
import { serverRead } from "@/lib/server-api";

/**
 * Membership administration. Only owners and admins are shown anything: the member list is read from FastAPI,
 * which refuses every other role (this check only avoids asking). Every change is decided by FastAPI from fresh
 * rows; this page just shows the current list and the actions the current role appears to allow.
 */
export default async function MembersPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  const credential = await requireCredential(`/o/${orgId}`);
  const memberships = await getMemberships(credential);
  const role = memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Members</h1>
      {role !== undefined && canAdminister(role) ? (
        <>
          <MembersAdmin key={orgId} members={await serverRead<Member[]>(orgId, "/api/members")} actorRole={role} />
          <InvitationsAdmin key={`invitations-${orgId}`} invitations={await serverRead<Invitation[]>(orgId, "/api/invitations")} actorRole={role} />
          <InvitationHistory
            invitations={await serverRead<Invitation[]>(orgId, "/api/invitations", "?closed=true")}
            timeZone={(await serverRead<Organization>(orgId, "/api/organization")).timezone}
          />
        </>
      ) : (
        <Notice testId="members-not-allowed">Only an owner or admin can manage members.</Notice>
      )}
    </div>
  );
}
