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
  created,
}: {
  organizations: Membership[];
  currentId: string;
  canCreate?: boolean;
  /** How many organizations this account has created and may create ("Created 1 / 1"). */
  created?: { count: number; max: number };
}) {
  return (
    <nav aria-label="Organizations" data-testid="org-switcher" className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-xs text-muted">Organization</span>
      {organizations.map((organization) =>
        organization.id === currentId ? (
          <span key={organization.id} aria-current="true" className="rounded-lg border border-border-strong bg-surface-hover px-2.5 py-1 font-medium">
            {organization.name}
          </span>
        ) : (
          // A plain anchor on purpose: see the component comment (full navigation).
          <a key={organization.id} href={`/o/${organization.id}`} className="rounded-lg px-2.5 py-1 text-secondary hover:bg-surface-hover hover:text-foreground">
            {organization.name}
          </a>
        ),
      )}
      {created && (
        <span className="text-xs text-muted" data-testid="created-count">
          Created {created.count} / {created.max}
        </span>
      )}
      {canCreate && (
        // Presentation only (FastAPI decides). A plain anchor: creation happens outside any organization.
        <a href="/organizations/new" className="rounded-lg border border-dashed border-border-strong px-2.5 py-1 text-secondary hover:bg-surface-hover hover:text-foreground" data-testid="create-organization-link">
          Create organization
        </a>
      )}
    </nav>
  );
}
