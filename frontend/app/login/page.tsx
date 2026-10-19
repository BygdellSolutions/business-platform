import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { SECONDARY_LINK } from "@/components/ui/Button";
import { LoginForm } from "@/features/auth/LoginForm";
import { authMode } from "@/lib/auth/config";
import { getCredential } from "@/lib/auth/credential";
import { safeNext } from "@/lib/auth/safe-next";
import { getCurrentUser } from "@/lib/orgs";

// Evaluated when a request arrives, never at build time (see next.config.test.ts and the image tests).
export const dynamic = "force-dynamic";

/** The real login (session mode only). The return path is validated here and again by the BFF. */
export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  if (authMode() !== "session") notFound();
  const { next, notice } = await searchParams;
  const destination = safeNext(typeof next === "string" ? next : undefined);

  // Already signed in: go on. Only a session the backend accepts counts, so a stale cookie shows the form instead of looping.
  const credential = await getCredential();
  if (credential !== null && (await getCurrentUser(credential)).status === "ok") redirect(destination);

  return (
    <main className="mx-auto flex max-w-sm flex-col gap-4 p-8">
      <h1 className="text-[28px] leading-tight font-semibold tracking-tight">Sign in</h1>
      <LoginForm next={destination} notice={typeof notice === "string" ? notice : undefined} />
      <p className="text-sm text-secondary">
        Cannot sign in? <Link href="/support" className="underline hover:text-foreground" data-testid="login-support">Get help</Link>.
      </p>
      <Link href="/?home" prefetch={false} className={SECONDARY_LINK}>
        Go to homepage
      </Link>
    </main>
  );
}
