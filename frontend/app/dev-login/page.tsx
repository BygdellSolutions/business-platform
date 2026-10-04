import { notFound } from "next/navigation";

import { devIdentityEnabled } from "@/lib/auth/config";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";

const MESSAGES: Record<string, string> = {
  invalid: "That does not look like an email address.",
  unknown: "The backend does not know an active user with that email.",
  unavailable: "The backend could not be reached.",
};

/** The seeded development users (see backend/app/scripts/seed_dev.py). */
const PRESETS = ["fredrik@dev.test", "maria@dev.test"];

/** Development-only sign-in. Not found unless AUTH_MODE=dev with APP_ENV=development on the server. */
export default async function DevLogin({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  if (!devIdentityEnabled()) notFound();
  const { error } = await searchParams;

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Development sign-in</h1>
      <p className="text-sm text-zinc-500">
        Development only. Pick a seeded user, or type the email of any user the backend knows. There are no passwords yet.
      </p>
      {error && MESSAGES[error] && <Notice tone="error" testId="login-error">{MESSAGES[error]}</Notice>}
      <ul className="flex flex-col gap-2">
        {PRESETS.map((email) => (
          <li key={email}>
            <form action="/api/dev-session" method="post">
              <input type="hidden" name="email" value={email} />
              <Button type="submit" data-testid={`login-as-${email}`}>Sign in as {email}</Button>
            </form>
          </li>
        ))}
      </ul>
      <form action="/api/dev-session" method="post" className="flex items-center gap-2">
        <label className="flex items-center gap-2 text-sm">
          Email
          <input name="email" type="email" required className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
        </label>
        <Button type="submit">Sign in</Button>
      </form>
    </main>
  );
}
