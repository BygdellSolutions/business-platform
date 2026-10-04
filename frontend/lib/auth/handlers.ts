import "server-only";

import { NextResponse, type NextRequest } from "next/server";

import { authConfig } from "@/lib/auth/config";
import { isToken } from "@/lib/auth/cookies";
import { credentialFromRequest } from "@/lib/auth/credential";
import { clientAddress, originProblem, validPreAuth } from "@/lib/auth/request";
import { clearPreAuthCookie, setSessionCookies } from "@/lib/auth/session-cookies";
import { backendFetch } from "@/lib/backend";

/** Shared parts of the authentication route handlers (login, setup, logout). */

export const NO_STORE = { "cache-control": "no-store" };
const MAX_BODY_BYTES = 16_384;

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): NextResponse {
  return NextResponse.json(body, { status, headers: { ...NO_STORE, ...headers } });
}

export function problem(status: number, code: string, message: string, headers: Record<string, string> = {}): NextResponse {
  return json({ detail: { code, message } }, status, headers);
}

/** These endpoints exist only in session mode; anywhere else they are not found. */
export function sessionModeOnly(): NextResponse | null {
  return authConfig().mode === "session" ? null : json({ detail: "Not found" }, 404);
}

/**
 * The pre-authentication contract for a request that has no session yet: the browser Origin is the canonical
 * origin, and the pre-auth header echoes the pre-auth cookie. Checked BEFORE anything reaches FastAPI. The
 * pre-auth secret is only compared here; it is never forwarded and never an authentication credential.
 */
export function preAuthProblem(request: NextRequest): NextResponse | null {
  if (originProblem(request) !== null) return problem(403, "forbidden_origin", "This request was not accepted.");
  if (!validPreAuth(request)) return problem(403, "pre_auth_failed", "This page has expired. Reload it and try again.");
  return null;
}

/** The JSON object in the body, or null (wrong type, too large, not an object). */
export async function readJsonObject(request: NextRequest): Promise<Record<string, unknown> | null> {
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) return null;
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) return null;
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** What FastAPI says on a successful login or setup: a session. Anything else is not trusted. */
export function parseSession(value: unknown): { token: string; csrf: string; expiresAt: Date } | null {
  if (typeof value !== "object" || value === null) return null;
  const { token, csrf_token: csrf, expires_at: expires } = value as Record<string, unknown>;
  if (!isToken(token as string) || !isToken(csrf as string) || typeof expires !== "string") return null;
  const expiresAt = new Date(expires);
  return Number.isNaN(expiresAt.getTime()) ? null : { token: token as string, csrf: csrf as string, expiresAt };
}

export function retryAfter(upstream: Response): Record<string, string> {
  const value = upstream.headers.get("retry-after");
  return value !== null && /^[0-9]{1,5}$/.test(value) ? { "retry-after": value } : {};
}

/** Call a pre-session FastAPI endpoint (login, setup) the way the BFF does: nothing from the client but the body. */
export async function callAuthEndpoint(request: NextRequest, path: string, body: Record<string, unknown>): Promise<Response> {
  return backendFetch(
    // A session the browser presents is passed on only so FastAPI can end it (fixation); it comes from the cookie.
    { credential: credentialFromRequest(request) },
    path,
    { method: "POST", body: JSON.stringify(body), clientAddress: clientAddress(request.headers) ?? undefined },
  );
}

/** A successful login or setup: set the protected cookies, consume the pre-auth cookie, say where to go. */
export function signedIn(session: { token: string; csrf: string; expiresAt: Date }, next: string): NextResponse {
  const response = json({ next });
  setSessionCookies(response, session);
  clearPreAuthCookie(response);
  return response;
}
