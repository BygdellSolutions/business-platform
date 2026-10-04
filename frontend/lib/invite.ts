/**
 * Small, pure helpers for the invitation flow (shared by the BFF routes and the pages).
 *
 * The invitation secret is a bearer token in the URL FRAGMENT of `/invite#<token>`. A fragment is never sent to a
 * server, so nothing server-side (a server component, a log, a Referer, a redirect target) ever sees it; the page
 * reads it once in the browser and removes it from the address at once.
 */

/** The same shape as every other token of the platform: 32 random bytes, base64url, 43 characters. */
export const INVITE_TOKEN = /^[A-Za-z0-9_-]{43}$/;

export function isInviteToken(value: unknown): value is string {
  return typeof value === "string" && INVITE_TOKEN.test(value);
}

/** The link an administrator copies. Built in the browser from its own origin; the secret stays in the fragment. */
export function inviteLink(origin: string, token: string): string {
  return `${origin}/invite#${token}`;
}

/** The fragment (without "#") if it is a well-formed token, null if it is something else, undefined if empty. */
export function tokenFromFragment(hash: string): string | null | undefined {
  const fragment = hash.replace(/^#/, "");
  if (fragment === "") return undefined;
  return isInviteToken(fragment) ? fragment : null;
}

/** Normalization the backend also applies (trim, lowercase): used only to COMPARE for the interface; FastAPI decides. */
export function normalizeEmail(value: string): string {
  return value.trim().toLowerCase();
}
