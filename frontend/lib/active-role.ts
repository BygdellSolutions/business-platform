import "server-only";

import type { Role } from "@/lib/api/types";
import { requireCredential } from "@/lib/auth/credential";
import { getMemberships } from "@/lib/orgs";

/**
 * The user's role in the organization of the URL, for deciding which controls a page offers.
 * Undefined when the memberships could not be read, which offers nothing. Presentation only: FastAPI
 * judges every request from the membership itself. Memoized per request through `getMemberships`, so
 * the layout and the page share one call.
 */
export async function readActiveRole(orgId: string): Promise<Role | undefined> {
  const memberships = await getMemberships(await requireCredential(`/o/${orgId}`));
  return memberships.status === "ok" ? memberships.memberships.find((membership) => membership.id === orgId)?.role : undefined;
}
