import { redirect } from "next/navigation";

import { Notice } from "@/components/ui/Notice";
import { ChangePasswordForm } from "@/features/account/ChangePasswordForm";
import { authMode } from "@/lib/auth/config";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser } from "@/lib/orgs";

export const dynamic = "force-dynamic"; // depends on AUTH_MODE at run time, never on the build's environment

/**
 * The signed-in person's own account and security settings. Not under `/o/{id}`: a password belongs to the
 * person, not to any organization they work in.
 */
export default async function AccountPage() {
  const credential = await requireCredential("/account");
  const current = await getCurrentUser(credential);
  if (current.status === "unauthorized") redirect(loginPath("/account"));
  if (current.status === "unavailable") throw new Error("The backend is unavailable");

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 p-8">
      <div>
        <h1 className="text-2xl font-semibold">Account security</h1>
        <p className="text-sm text-zinc-500" data-testid="account-email">
          Signed in as {current.user.email}
        </p>
      </div>
      <section className="flex flex-col gap-3" aria-label="Change password">
        <h2 className="text-lg font-semibold">Change password</h2>
        {authMode() === "session" ? (
          <ChangePasswordForm />
        ) : (
          <Notice testId="no-passwords">This environment uses the development sign-in, which has no passwords.</Notice>
        )}
      </section>
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/" className="text-sm underline">Back to your organizations</a>
    </main>
  );
}
