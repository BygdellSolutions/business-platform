import "server-only";

import { cookies } from "next/headers";

/**
 * Development identity. There is no login yet: a developer picks a seeded user on /dev-login
 * and the choice is kept in an httpOnly cookie that only server code can read. Everything that
 * needs "who is calling" goes through this module, so replacing it with real authentication
 * later (a session cookie resolved to a backend credential) does not touch any feature.
 *
 * Fails closed: unless DEV_IDENTITY=enabled is set on the server, there is no identity at all.
 */
export const DEV_USER_COOKIE = "bp_dev_user";
export const DEV_USER_MAX_AGE_SECONDS = 60 * 60 * 8;

const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{1,63}$/;

export function devIdentityEnabled(): boolean {
  return process.env.DEV_IDENTITY === "enabled";
}

/** A normalized email, or null if it is not a plausible email. */
export function parseEmail(value: string | undefined | null): string | null {
  const email = value?.trim().toLowerCase() ?? "";
  return email.length <= 320 && EMAIL.test(email) ? email : null;
}

/** The identity for a cookie value (shared by server components and the BFF route). */
export function identityFromCookie(value: string | undefined): string | null {
  return devIdentityEnabled() ? parseEmail(value) : null;
}

/** The current dev user (email) in a server component, or null. */
export async function getIdentity(): Promise<string | null> {
  const store = await cookies();
  return identityFromCookie(store.get(DEV_USER_COOKIE)?.value);
}
