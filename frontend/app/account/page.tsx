import { redirect } from "next/navigation";

import { Notice } from "@/components/ui/Notice";
import { ChangePasswordForm } from "@/features/account/ChangePasswordForm";
import { AccountDataPanel } from "@/features/account/AccountDataPanel";
import { MfaPanel } from "@/features/account/MfaPanel";
import { SessionsPanel } from "@/features/account/SessionsPanel";
import { authMode } from "@/lib/auth/config";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { getCurrentUser, getMfaStatus, getOwnSessions, needsMfaSetup } from "@/lib/orgs";
import { SECONDARY_LINK } from "@/components/ui/Button";

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

  const own = authMode() === "session" ? await getOwnSessions(credential) : null;
  if (own?.status === "unauthorized") redirect(loginPath("/account"));
  const mfa = authMode() === "session" ? await getMfaStatus(credential) : null;
  if (mfa?.status === "unauthorized") redirect(loginPath("/account"));

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 p-8">
      <div>
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Account security</h1>
        <p className="text-sm text-zinc-500" data-testid="account-email">
          Signed in as {current.user.email}
        </p>
      </div>
      <section className="panel flex max-w-xl flex-col gap-3" aria-label="Change password">
        <h2 className="text-lg font-semibold">Change password</h2>
        {authMode() === "session" ? (
          <ChangePasswordForm />
        ) : (
          <Notice testId="no-passwords">This environment uses the development sign-in, which has no passwords.</Notice>
        )}
      </section>
      {mfa !== null && (
        <section className="panel flex max-w-xl flex-col gap-3" aria-label="Second factor">
          <h2 className="text-lg font-semibold">Second factor</h2>
          {mfa.status === "ok" ? (
            <MfaPanel initial={mfa.mfa} mustSetUp={needsMfaSetup(current.user)} />
          ) : (
            <Notice tone="error" testId="mfa-unavailable">The second factor could not be loaded.</Notice>
          )}
        </section>
      )}
      {authMode() === "session" && (
        <section className="panel flex max-w-xl flex-col gap-3" aria-label="Where you are signed in">
          <h2 className="text-lg font-semibold">Where you are signed in</h2>
          {own?.status === "ok" ? <SessionsPanel initial={own.sessions} /> : <Notice tone="error" testId="sessions-unavailable">Your sessions could not be loaded.</Notice>}
        </section>
      )}
      {authMode() === "session" && (
        <section className="panel flex max-w-xl flex-col gap-3" aria-label="Your data">
          <h2 className="text-lg font-semibold">Your data</h2>
          <AccountDataPanel />
        </section>
      )}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/" className={`${SECONDARY_LINK} self-start`}>Back to your organizations</a>
    </main>
  );
}
