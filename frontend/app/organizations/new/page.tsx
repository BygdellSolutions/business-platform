import { redirect } from "next/navigation";

import { Notice } from "@/components/ui/Notice";
import { CreateOrganizationForm } from "@/features/organizations/CreateOrganizationForm";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser } from "@/lib/orgs";

/**
 * Create an organization. Not under `/o/{id}`: there is no organization yet. Authentication identifies the user;
 * `can_create_organizations` (an account property set by an operator) decides whether the form is shown. Hiding
 * the form is a convenience: FastAPI refuses the request for any other account.
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
        <Notice testId="creation-not-allowed">This account is not allowed to create organizations. Ask the person who runs this service.</Notice>
      )}
      {/* A plain anchor: leaving this page is a full navigation, like every organization switch. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/" className="text-sm underline">Back to your organizations</a>
    </main>
  );
}
