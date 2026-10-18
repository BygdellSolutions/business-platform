import type { Metadata } from "next";
import Link from "next/link";

import { PRIMARY_LINK, SECONDARY_LINK } from "@/components/ui/Button";
import { CopyableValue } from "@/components/ui/CopyableValue";
import { getCredential, loginPath } from "@/lib/auth/credential";
import { getCurrentUser } from "@/lib/orgs";
import { SUPPORT_EMAIL, supportMailto } from "@/lib/support";

export const metadata: Metadata = { title: "Support" };

// Depends on whether the visitor is signed in, so never decided at build time.
export const dynamic = "force-dynamic";

const INCLUDE = [
  "What you were trying to do, and what happened instead.",
  "The page you were on, and the name of the customer, order or invoice if there is one.",
  "A screenshot, if it helps. Please leave out passwords.",
  "The email address you sign in with, if you write from a different one.",
] as const;

/** Public: someone who cannot sign in must be able to reach it. A signed-in visitor gets their account written into the email. */
export default async function SupportPage() {
  const credential = await getCredential();
  const current = credential === null ? null : await getCurrentUser(credential);
  const who = current?.status === "ok" ? { name: current.user.name, email: current.user.email } : null;

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 p-8">
      <div>
        <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Support</h1>
        <p className="mt-1 text-secondary">Something not working, or not sure how to do something? Write to us and we will reply by email.</p>
      </div>

      <section className="panel flex flex-col gap-4" aria-label="Contact support">
        <div className="flex flex-wrap gap-3">
          <a href={supportMailto(who)} className={PRIMARY_LINK} data-testid="email-support">
            Email support
          </a>
        </div>
        <p className="text-sm text-secondary">
          This opens your email program with the subject and a few questions filled in. If nothing opens, write to the address below from any email
          account.
        </p>
        <CopyableValue label="Support email" value={SUPPORT_EMAIL} testId="support-address" />
        {who !== null && (
          <p className="text-sm text-secondary" data-testid="support-account">
            The email will mention your account ({who.email}) so we know who is asking.
          </p>
        )}
      </section>

      <section className="panel flex flex-col gap-2" aria-label="What to include">
        <h2 className="text-lg font-semibold">What helps us help you</h2>
        <ul className="list-disc pl-5 text-sm" data-testid="support-include">
          {INCLUDE.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      <div>
        <Link href={who !== null ? "/organizations" : loginPath()} className={SECONDARY_LINK}>
          {who !== null ? "Back to the app" : "Go to sign in"}
        </Link>
      </div>
    </main>
  );
}
