import { notFound } from "next/navigation";

import { SetupForm } from "@/features/auth/SetupForm";
import { authMode } from "@/lib/auth/config";

/**
 * Set a password with a single-use setup link (session mode only). The link secret is in the URL fragment, which
 * the form removes at once; the page itself carries no secret, loads nothing from another site, and is served
 * with `Cache-Control: no-store` and `Referrer-Policy: no-referrer` (see next.config.ts).
 */
export default function SetupPage() {
  if (authMode() !== "session") notFound();
  return (
    <main className="mx-auto flex max-w-sm flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Set your password</h1>
      <SetupForm />
    </main>
  );
}
