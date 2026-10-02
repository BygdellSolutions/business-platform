"use client";

import { createContext, useContext, type ReactNode } from "react";

const OrgContext = createContext<string | null>(null);

/**
 * Marks everything below as belonging to ONE organization, taken from the URL.
 *
 * The `key` makes React discard the whole subtree (all client state) whenever the
 * organization changes, so state from one tenant can never survive into another even if a
 * switch ever happens through client-side navigation. (The switcher uses full navigations
 * anyway; this is the second guard.)
 */
export function OrgScope({ orgId, children }: { orgId: string; children: ReactNode }) {
  return (
    <OrgContext.Provider key={orgId} value={orgId}>
      {children}
    </OrgContext.Provider>
  );
}

/** The organization of the current page. Client components fetch tenant data with this id. */
export function useOrgId(): string {
  const orgId = useContext(OrgContext);
  if (orgId === null) throw new Error("useOrgId must be used inside <OrgScope>");
  return orgId;
}
