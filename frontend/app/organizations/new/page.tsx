import { redirect } from "next/navigation";

import { Notice } from "@/components/ui/Notice";
import { CreateOrganizationForm } from "@/features/organizations/CreateOrganizationForm";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser } from "@/lib/orgs";

/**
 * Create an organization. Not under `/o/{id}`: there is no organization yet. Authentication identifies the user; the
 * account's owned-organization limit ("Owned 1 / 1") decides whether the form is shown. Hiding the form is a
 * convenience: FastAPI refuses the request when the limit is reached.
 */
export default async function NewOrganizationPage() {
  const credential = await requireCredential();
  const current = await getCurrentUser(credential);
  if (current.status === "unauthorized") redirect(loginPath());
  if (current.status === "unavailable") throw new Error("The backend is unavailable");

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Create an organization</h1>
      {current.user.can_create_organizations ? (
        <>
          <p className="text-sm text-zinc-500">You will be its owner. You can complete the business profile later in Settings.</p>
          <CreateOrganizationForm />
        </>
      ) : (
        <Notice testId="creation-not-allowed">
          You own {current.user.owned_organizations} of the {current.user.max_owned_organizations} organization(s) this account may own. Owning another
          one needs a larger allowance: ask the person who runs this service.
        </Notice>
      )}
      {/* A plain anchor: leaving this page is a full navigation, like every organization switch. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/" className="text-sm underline">Back to your organizations</a>
    </main>
  );
}
