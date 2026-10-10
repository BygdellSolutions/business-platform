import "server-only";

import { cache } from "react";

import type { Credential } from "@/lib/auth/credential";
import { backendFetch } from "@/lib/backend";
import type { Membership } from "@/lib/api/types";

export type MembershipsResult =
  | { status: "ok"; memberships: Membership[] }
  | { status: "unauthorized" }
  | { status: "unavailable" };

/**
 * The organizations this user belongs to, from FastAPI (the authority). Memoized per request (the credential
 * object is one per request), so a layout and a page asking for it cost one call.
 */
export const getMemberships = cache(async (credential: Credential): Promise<MembershipsResult> => {
  let response: Response;
  try {
    response = await backendFetch({ credential }, "/api/me/organizations");
  } catch {
    return { status: "unavailable" };
  }
  if (response.status === 401) return { status: "unauthorized" };
  if (!response.ok) return { status: "unavailable" };
  return { status: "ok", memberships: (await response.json()) as Membership[] };
});

export interface CurrentUser {
  id: string;
  email: string;
  name: string;
  /** Computed by FastAPI: whether this account owns fewer organizations than it may. */
  can_create_organizations: boolean;
  owned_organizations: number;
  max_owned_organizations: number;
}

export type CurrentUserResult = { status: "ok"; user: CurrentUser } | { status: "unauthorized" } | { status: "unavailable" };

/** Who the credential is, according to FastAPI: the shell shows this, never an email held by the browser. */
export const getCurrentUser = cache(async (credential: Credential): Promise<CurrentUserResult> => {
  let response: Response;
  try {
    response = await backendFetch({ credential }, "/api/me/user");
  } catch {
    return { status: "unavailable" };
  }
  if (response.status === 401) return { status: "unauthorized" };
  if (!response.ok) return { status: "unavailable" };
  return { status: "ok", user: (await response.json()) as CurrentUser };
});
