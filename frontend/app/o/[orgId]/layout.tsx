import { notFound, redirect } from "next/navigation";
import type { ReactNode } from "react";

import { isUuid } from "@/lib/backend";
import { authMode } from "@/lib/auth/config";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser, getMemberships, needsMfaSetup } from "@/lib/orgs";
import { OrgScope } from "@/components/shell/org-context";
import { OrgMenu } from "@/components/shell/OrgMenu";
import { QuickSearch } from "@/components/shell/QuickSearch";
import { UserMenu } from "@/components/shell/UserMenu";
import { MainNav } from "@/components/shell/MainNav";
import { NAV } from "@/components/shell/nav";

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
  if (needsMfaSetup(current.user)) redirect("/account?mfa=required"); // the server refuses every request until it is set up (FastAPI)

  const organization = result.memberships.find((membership) => membership.id === orgId);
  if (!organization) notFound();

  // The organization's mark in the sidebar: its initials ("Bygdell Solutions" -> "BS").
  const initials = organization.name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("");

  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-5 border-b border-border bg-sidebar p-3 md:sticky md:top-0 md:h-screen md:w-60 md:overflow-y-auto md:border-r md:border-b-0">
        <div className="flex items-center gap-3 px-2 pt-1">
          <span aria-hidden="true" className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-sm font-semibold text-accent-icon">
            {initials}
          </span>
          <div className="min-w-0">
            <div data-testid="org-name" className="truncate text-[15px] leading-tight font-semibold">{organization.name}</div>
            <div className="text-xs text-muted">
              Your role: <span data-testid="org-role" className="capitalize">{organization.role}</span>
            </div>
          </div>
        </div>
        <MainNav orgId={orgId} items={NAV.filter((item) => item.roles === undefined || item.roles.includes(organization.role))} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col bg-page">
        <header className="sticky top-0 z-20 grid min-h-14 grid-cols-[1fr_auto] items-center gap-x-3 gap-y-2 border-b border-border bg-topbar px-4 py-2 md:grid-cols-[1fr_minmax(0,36rem)_1fr] md:px-6">
          <div className="order-2 col-span-2 flex justify-center md:order-none md:col-span-1 md:col-start-2">
            <QuickSearch orgId={orgId} pages={NAV.filter((item) => item.enabled && (item.roles === undefined || item.roles.includes(organization.role))).map((item) => ({ label: item.label, path: item.path }))} />
          </div>
          <div className="order-1 col-start-2 flex items-center justify-end gap-3 md:order-none md:col-start-3">
            <OrgMenu organizations={result.memberships} currentId={orgId} canCreate={current.user.can_create_organizations} created={{ count: current.user.created_organizations, max: current.user.max_owned_organizations }} />
            <UserMenu name={current.user.name} email={current.user.email} mode={mode === "dev" || mode === "session" ? mode : null} />
          </div>
        </header>
        <main className="flex-1 p-4 md:p-6">
          <OrgScope orgId={orgId}>{children}</OrgScope>
        </main>
      </div>
    </div>
  );
}
