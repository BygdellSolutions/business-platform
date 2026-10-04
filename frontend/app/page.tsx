import { redirect } from "next/navigation";

import { SignOut } from "@/components/shell/SignOut";
import { Notice } from "@/components/ui/Notice";
import { authMode } from "@/lib/auth/config";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser, getMemberships } from "@/lib/orgs";

/** Entry point: pick the organization to work in. One organization is entered directly. */
export default async function Home() {
  const credential = await requireCredential();

  const [result, current] = await Promise.all([getMemberships(credential), getCurrentUser(credential)]);
  if (result.status === "unauthorized" || current.status === "unauthorized") redirect(loginPath());
  if (result.status === "unavailable" || current.status === "unavailable") throw new Error("The backend is unavailable");

  const { memberships } = result;
  if (memberships.length === 1) redirect(`/o/${memberships[0].id}`);
  const mode = authMode();

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Choose an organization</h1>
      <p className="text-sm text-zinc-500">
        Signed in as <span data-testid="user-email">{current.user.email}</span>
      </p>
      {memberships.length === 0 ? (
        <Notice testId="no-organizations">You do not belong to any organization yet.</Notice>
      ) : (
        <ul data-testid="organization-list" className="flex flex-col gap-2">
          {memberships.map((organization) => (
            <li key={organization.id}>
              <a href={`/o/${organization.id}`} className="underline">
                {organization.name}
              </a>{" "}
              <span className="text-sm text-zinc-500">({organization.role})</span>
            </li>
          ))}
        </ul>
      )}
      {(mode === "dev" || mode === "session") && <SignOut mode={mode} />}
    </main>
  );
}
