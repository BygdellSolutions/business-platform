import { notFound, redirect } from "next/navigation";
import type { ReactNode } from "react";

import { isUuid } from "@/lib/backend";
import { authMode } from "@/lib/auth/config";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser, getMemberships } from "@/lib/orgs";
import { OrgScope } from "@/components/shell/org-context";
import { OrgSwitcher } from "@/components/shell/OrgSwitcher";
import { NAV } from "@/components/shell/nav";
import { SignOut } from "@/components/shell/SignOut";

/**
 * The organization shell. The organization comes from the URL and is checked against the
 * user's own memberships (from FastAPI): an id that is malformed, nonexistent, or belongs to
 * someone else all end in the same 404. This check is a convenience for the UI; every data
 * request is verified again by FastAPI.
 */
export default async function OrgLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = await params;
  if (!isUuid(orgId)) notFound();

  const credential = await requireCredential(`/o/${orgId}`);
  const [result, current] = await Promise.all([getMemberships(credential), getCurrentUser(credential)]);
  if (result.status === "unauthorized" || current.status === "unauthorized") redirect(loginPath(`/o/${orgId}`));
  if (result.status === "unavailable" || current.status === "unavailable") throw new Error("The backend is unavailable");
  const mode = authMode();

  const organization = result.memberships.find((membership) => membership.id === orgId);
  if (!organization) notFound();

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex flex-col gap-2 border-b border-zinc-300 p-4 dark:border-zinc-700">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <span data-testid="org-name" className="text-xl font-semibold">{organization.name}</span>{" "}
            <span data-testid="org-role" className="text-sm text-zinc-500">{organization.role}</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span data-testid="user-email">{current.user.email}</span>
            <a href="/account" className="underline" data-testid="account-link">
              Account
            </a>
            {(mode === "dev" || mode === "session") && <SignOut mode={mode} />}
          </div>
        </div>
        <OrgSwitcher organizations={result.memberships} currentId={orgId} canCreate={current.user.can_create_organizations} />
        <nav aria-label="Main" className="flex flex-wrap gap-4 text-sm">
          {NAV.filter((item) => item.roles === undefined || item.roles.includes(organization.role)).map((item) =>
            item.enabled ? (
              <a key={item.label} href={`/o/${orgId}${item.path}`} className="underline">
                {item.label}
              </a>
            ) : (
              <span key={item.label} aria-disabled="true" className="text-zinc-400">
                {item.label} (coming soon)
              </span>
            ),
          )}
        </nav>
      </header>
      <main className="flex-1 p-4">
        <OrgScope orgId={orgId}>{children}</OrgScope>
      </main>
    </div>
  );
}
