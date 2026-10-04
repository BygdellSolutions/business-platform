import "server-only";

import { timingSafeEqual } from "node:crypto";
import { isIP } from "node:net";
import type { NextRequest } from "next/server";

import { authConfig, cookiePolicy, trustedProxyHops } from "@/lib/auth/config";
import { CSRF_HEADER, PRE_AUTH_HEADER, isToken } from "@/lib/auth/cookies";

/**
 * What the BFF checks about a BROWSER request before it speaks to FastAPI (the browser half of the split; the
 * session-bound CSRF check is FastAPI's and is never skipped here).
 */

/**
 * Session mode: a state-changing request must carry `Origin` and it must be exactly the configured canonical
 * origin (PUBLIC_ORIGIN). The `Host` header is not consulted, because it is whatever the client or a proxy
 * made it; a missing Origin is refused (every browser mutation carries one).
 */
export function originProblem(request: NextRequest): "unconfigured" | "missing" | "mismatch" | null {
  const { publicOrigin } = authConfig();
  if (publicOrigin === null) return "unconfigured";
  const origin = request.headers.get("origin");
  if (origin === null || origin === "") return "missing";
  return origin === publicOrigin ? null : "mismatch";
}

export function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

/** The double-submit check: the CSRF header must equal the readable CSRF cookie, both well-formed. */
export function validCsrf(request: NextRequest): string | null {
  const header = request.headers.get(CSRF_HEADER);
  const cookie = request.cookies.get(cookiePolicy().name("csrf"))?.value;
  if (!isToken(header) || !isToken(cookie)) return null;
  return safeEqual(header, cookie) ? header : null;
}

/** The pre-authentication double-submit (login, setup): the header must equal the pre-auth cookie. */
export function validPreAuth(request: NextRequest): boolean {
  const header = request.headers.get(PRE_AUTH_HEADER);
  const cookie = request.cookies.get(cookiePolicy().name("pre"))?.value;
  return isToken(header) && isToken(cookie) && safeEqual(header, cookie);
}

/**
 * The client's address as the BFF itself can establish it, for FastAPI's login throttling, or null.
 *
 * Only with TRUSTED_PROXY_HOPS = N >= 1 (N reverse proxies that each APPEND the address they saw to
 * `X-Forwarded-For`): the entry N places from the right is the address the outermost trusted proxy saw. Entries
 * further left are whatever the client wrote and are never used, so the browser cannot choose the value.
 * With 0 hops (the default) nothing is forwarded. FastAPI trusts the result only with
 * TRUST_CLIENT_IP_HEADER=true, which is valid only while FastAPI is reachable from the BFF alone.
 */
export function clientAddress(headers: Headers, hops: number = trustedProxyHops()): string | null {
  if (hops < 1) return null;
  const entries = (headers.get("x-forwarded-for") ?? "").split(",").map((entry) => entry.trim());
  if (entries.length < hops) return null;
  const candidate = entries[entries.length - hops];
  return isIP(candidate) !== 0 ? candidate : null;
}
