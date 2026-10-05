import { notFound } from "next/navigation";

import { InviteFlow } from "@/features/invite/InviteFlow";
import { authMode } from "@/lib/auth/config";
import { getCredential } from "@/lib/auth/credential";
import { getCurrentUser } from "@/lib/orgs";

// Evaluated when a request arrives, never at build time (see next.config.test.ts and the image tests).
export const dynamic = "force-dynamic";

/**
 * Accept an invitation (session mode only). The page is rendered WITHOUT the secret: the invitation token is in the
 * URL fragment, which never reaches the server, so no server component, log or redirect can see it; the client
 * component reads and removes it. The only thing the server contributes is who (if anyone) is signed in, so the
 * client can offer "join" or "sign out and continue". Served with `Cache-Control: no-store` and
 * `Referrer-Policy: no-referrer` (next.config.ts); it loads nothing from another site.
 */
export default async function InvitePage() {
  if (authMode() !== "session") notFound();
  const credential = await getCredential();
  let signedInEmail: string | null = null;
  if (credential !== null) {
    const current = await getCurrentUser(credential);
    if (current.status === "ok") signedInEmail = current.user.email;
  }
  return (
    <main className="mx-auto flex max-w-md flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Join an organization</h1>
      <InviteFlow signedInEmail={signedInEmail} />
    </main>
  );
}
