import { notFound, redirect } from "next/navigation";
import type { ReactNode } from "react";

import { isUuid } from "@/lib/backend";
import { authMode } from "@/lib/auth/config";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser, getMemberships } from "@/lib/orgs";
import { OrgScope } from "@/components/shell/org-context";
import { OrgSwitcher } from "@/components/shell/OrgSwitcher";
import { MainNav } from "@/components/shell/MainNav";
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
    <div className="flex min-h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-4 border-b border-zinc-200 bg-white p-4 md:sticky md:top-0 md:h-screen md:w-60 md:overflow-y-auto md:border-r md:border-b-0 dark:border-zinc-800 dark:bg-zinc-950">
        <div className="px-3">
          <div data-testid="org-name" className="text-lg leading-tight font-semibold">{organization.name}</div>
          <div className="text-sm text-zinc-500">
            Your role: <span data-testid="org-role" className="capitalize">{organization.role}</span>
          </div>
        </div>
        <MainNav orgId={orgId} items={NAV.filter((item) => item.roles === undefined || item.roles.includes(organization.role))} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-zinc-200 px-3 pt-4 text-sm md:mt-auto dark:border-zinc-800">
          <span data-testid="user-email" className="w-full truncate text-zinc-600 dark:text-zinc-400">{current.user.email}</span>
          <a href="/account" className="underline" data-testid="account-link">
            Account
          </a>
          {(mode === "dev" || mode === "session") && <SignOut mode={mode} />}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col bg-page">
        <div className="border-b border-zinc-200 px-4 py-2 md:px-8 dark:border-zinc-800">
          <OrgSwitcher organizations={result.memberships} currentId={orgId} canCreate={current.user.can_create_organizations} owned={{ count: current.user.owned_organizations, max: current.user.max_owned_organizations }} />
        </div>
        <main className="flex-1 p-4 md:p-8">
          <OrgScope orgId={orgId}>{children}</OrgScope>
        </main>
      </div>
    </div>
  );
}
