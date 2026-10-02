import { notFound, redirect } from "next/navigation";
import type { ReactNode } from "react";

import { isUuid } from "@/lib/backend";
import { devIdentityEnabled, getIdentity } from "@/lib/identity";
import { getMemberships } from "@/lib/orgs";
import { OrgScope } from "@/components/shell/org-context";
import { OrgSwitcher } from "@/components/shell/OrgSwitcher";
import { NAV } from "@/components/shell/nav";
import { Button } from "@/components/ui/Button";

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

  const email = await getIdentity();
  if (email === null) redirect("/dev-login");

  const result = await getMemberships(email);
  if (result.status === "unauthorized") redirect("/dev-login");
  if (result.status === "unavailable") throw new Error("The backend is unavailable");

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
            <span data-testid="user-email">{email}</span>
            {devIdentityEnabled() && (
              <form action="/api/dev-session" method="post">
                <input type="hidden" name="logout" value="1" />
                <Button type="submit">Sign out</Button>
              </form>
            )}
          </div>
        </div>
        <OrgSwitcher organizations={result.memberships} currentId={orgId} />
        <nav aria-label="Main" className="flex flex-wrap gap-4 text-sm">
          {NAV.map((item) =>
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
