import { redirect } from "next/navigation";

import { devIdentityEnabled, getIdentity } from "@/lib/identity";
import { getMemberships } from "@/lib/orgs";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";

/** Entry point: pick the organization to work in. One organization is entered directly. */
export default async function Home() {
  const email = await getIdentity();
  if (email === null) redirect("/dev-login");

  const result = await getMemberships(email);
  if (result.status === "unauthorized") redirect("/dev-login");
  if (result.status === "unavailable") throw new Error("The backend is unavailable");

  const { memberships } = result;
  if (memberships.length === 1) redirect(`/o/${memberships[0].id}`);

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Choose an organization</h1>
      <p className="text-sm text-zinc-500">
        Signed in as <span data-testid="user-email">{email}</span>
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
      {devIdentityEnabled() && (
        <form action="/api/dev-session" method="post">
          <input type="hidden" name="logout" value="1" />
          <Button type="submit">Sign out</Button>
        </form>
      )}
    </main>
  );
}
