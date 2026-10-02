import "server-only";

import { cache } from "react";

import { backendFetch } from "@/lib/backend";
import type { Membership } from "@/lib/api/types";

export type MembershipsResult =
  | { status: "ok"; memberships: Membership[] }
  | { status: "unauthorized" }
  | { status: "unavailable" };

/**
 * The organizations this user belongs to, from FastAPI (the authority). Memoized per request,
 * so a layout and a page asking for it cost one call.
 */
export const getMemberships = cache(async (email: string): Promise<MembershipsResult> => {
  let response: Response;
  try {
    response = await backendFetch({ email }, "/api/me/organizations");
  } catch {
    return { status: "unavailable" };
  }
  if (response.status === 401) return { status: "unauthorized" };
  if (!response.ok) return { status: "unavailable" };
  return { status: "ok", memberships: (await response.json()) as Membership[] };
});
