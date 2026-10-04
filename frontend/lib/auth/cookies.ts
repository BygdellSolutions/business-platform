/**
 * Browser-side constants for the authentication cookies and headers (safe to import from client code).
 *
 * The SESSION cookie is HttpOnly: no script can read it, and nothing here names it for reading. Only the CSRF
 * cookie is readable, because the double-submit scheme needs the page to echo it in a header.
 */

export const CSRF_HEADER = "x-csrf-token";
export const PRE_AUTH_HEADER = "x-pre-auth";
export const CSRF_COOKIE_NAMES = ["__Host-bp_csrf", "bp_csrf"] as const;

/** The CSRF token from a `document.cookie` string, if there is one. */
export function readCsrfToken(cookieString: string): string | null {
  for (const part of cookieString.split(";")) {
    const [rawName, ...rest] = part.trim().split("=");
    if ((CSRF_COOKIE_NAMES as readonly string[]).includes(rawName)) return rest.join("=") || null;
  }
  return null;
}

/** A 256-bit token as the backend makes them (URL-safe base64, 43 characters). */
export const TOKEN_SHAPE = /^[A-Za-z0-9_-]{43}$/;

export function isToken(value: string | null | undefined): value is string {
  return typeof value === "string" && TOKEN_SHAPE.test(value);
}
