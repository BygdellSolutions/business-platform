import type { NextRequest } from "next/server";

/**
 * CSRF defense for state-changing requests: when a browser sends an `Origin` header (it does
 * for cross-site and for same-origin form posts and fetches), its host must be the host the
 * browser used to reach us. The comparison is with the `Host` header, which is what the
 * browser actually requested; Next.js may normalize `request.nextUrl.host` (for example to
 * `localhost` when started with `--hostname 127.0.0.1`), which would reject legitimate
 * same-origin requests.
 *
 * A request without `Origin` is not a browser form post or cross-site fetch (those always
 * carry one), so it passes; the identity cookie is also SameSite=Lax and bodies must be JSON.
 * Behind a reverse proxy, forward the original Host header.
 */
export function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (origin === null) return true;
  const host = request.headers.get("host") ?? request.nextUrl.host;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
