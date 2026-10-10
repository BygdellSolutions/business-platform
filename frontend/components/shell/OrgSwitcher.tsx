import type { Membership } from "@/lib/api/types";

/**
 * Lists the user's organizations. Each entry is a plain link (a FULL navigation, not a client
 * transition): switching organization reloads the page, so no client state, router cache entry
 * or in-flight request from the previous organization can survive.
 */
export function OrgSwitcher({
  organizations,
  currentId,
  canCreate = false,
  owned,
}: {
  organizations: Membership[];
  currentId: string;
  canCreate?: boolean;
  /** How many organizations this account owns and may own ("Owned 1 / 1"). */
  owned?: { count: number; max: number };
}) {
  return (
    <nav aria-label="Organizations" data-testid="org-switcher" className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-zinc-500">Organization:</span>
      {organizations.map((organization) =>
        organization.id === currentId ? (
          <span key={organization.id} aria-current="true" className="rounded bg-zinc-900 px-2 py-0.5 text-white dark:bg-zinc-100 dark:text-zinc-900">
            {organization.name}
          </span>
        ) : (
          // A plain anchor on purpose: see the component comment (full navigation).
          <a key={organization.id} href={`/o/${organization.id}`} className="rounded border px-2 py-0.5 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            {organization.name}
          </a>
        ),
      )}
      {owned && (
        <span className="text-xs text-zinc-500" data-testid="owned-count">
          Owned {owned.count} / {owned.max}
        </span>
      )}
      {canCreate && (
        // Presentation only (FastAPI decides). A plain anchor: creation happens outside any organization.
        <a href="/organizations/new" className="rounded border border-dashed px-2 py-0.5 hover:bg-zinc-100 dark:hover:bg-zinc-800" data-testid="create-organization-link">
          Create organization
        </a>
      )}
    </nav>
  );
}
