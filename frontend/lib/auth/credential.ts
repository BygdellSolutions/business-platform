import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { NextRequest } from "next/server";
import { cache } from "react";

import { authConfig, cookiePolicy } from "@/lib/auth/config";
import { isToken } from "@/lib/auth/cookies";
import { safeNext } from "@/lib/auth/safe-next";
import { DEV_USER_COOKIE, parseEmail } from "@/lib/identity";

/**
 * What the server holds about the caller, and the ONLY thing that is turned into upstream authentication.
 *
 *   dev      the dev user's email (cookie), sent as X-Dev-User-Email: an identity SELECTOR for development only
 *   session  the opaque session token (HttpOnly cookie), sent as Authorization: Bearer
 *
 * It is never an authorization decision: FastAPI resolves the token (or dev header) to a user and the
 * organization selector to a membership. Business code never inspects it.
 */
export type Credential = { kind: "dev"; email: string } | { kind: "session"; token: string };

export function credentialFromCookies(read: (name: string) => string | undefined): Credential | null {
  const { mode } = authConfig();
  if (mode === "dev") {
    const email = parseEmail(read(DEV_USER_COOKIE));
    return email === null ? null : { kind: "dev", email };
  }
  if (mode === "session") {
    const token = read(cookiePolicy().name("session"));
    return isToken(token) ? { kind: "session", token } : null;
  }
  return null;
}

export function credentialFromRequest(request: NextRequest): Credential | null {
  return credentialFromCookies((name) => request.cookies.get(name)?.value);
}

/** The credential of the current server-rendered request (one object per request, so it can key memoized reads). */
export const getCredential = cache(async (): Promise<Credential | null> => {
  const store = await cookies();
  return credentialFromCookies((name) => store.get(name)?.value);
});

/** Where an unauthenticated browser goes: the real login (session mode) or the dev login, with a safe way back. */
export function loginPath(returnTo?: string): string {
  const { mode } = authConfig();
  if (mode === "dev") return "/dev-login";
  const next = returnTo === undefined ? "/" : safeNext(returnTo);
  return next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
}

export async function requireCredential(returnTo?: string): Promise<Credential> {
  const credential = await getCredential();
  if (credential === null) redirect(loginPath(returnTo));
  return credential;
}
