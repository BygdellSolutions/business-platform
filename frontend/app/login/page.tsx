import { notFound } from "next/navigation";

import { LoginForm } from "@/features/auth/LoginForm";
import { authMode } from "@/lib/auth/config";
import { safeNext } from "@/lib/auth/safe-next";

// Evaluated when a request arrives, never at build time (see next.config.test.ts and the image tests).
export const dynamic = "force-dynamic";

/** The real login (session mode only). The return path is validated here and again by the BFF. */
export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  if (authMode() !== "session") notFound();
  const { next, notice } = await searchParams;

  return (
    <main className="mx-auto flex max-w-sm flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Sign in</h1>
      <LoginForm next={safeNext(typeof next === "string" ? next : undefined)} notice={typeof notice === "string" ? notice : undefined} />
    </main>
  );
}
