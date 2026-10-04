import type { NextRequest } from "next/server";

import { credentialFromRequest } from "@/lib/auth/credential";
import { json, problem, sessionModeOnly } from "@/lib/auth/handlers";
import { originProblem, validCsrf } from "@/lib/auth/request";
import { clearSessionCookies } from "@/lib/auth/session-cookies";
import { backendFetch } from "@/lib/backend";

/**
 * Logout: a CSRF-protected state change (Origin equal to PUBLIC_ORIGIN and the CSRF double-submit), so another
 * site cannot sign a user out.
 *
 * Once it is accepted THIS BROWSER is signed out whatever FastAPI answers: the cookies are always cleared (with
 * the attributes they were set with). What the response says about the server is honest:
 *   "confirmed"        FastAPI ended the session (204)
 *   "already_invalid"  FastAPI did not know the session any more (401): it cannot be used
 *   "unconfirmed"      FastAPI could not be reached or answered something else: the browser is signed out, but
 *                      the session may still be valid on the server until it expires
 */
export async function POST(request: NextRequest) {
  const refused = sessionModeOnly();
  if (refused) return refused;
  if (originProblem(request) !== null) return problem(403, "forbidden_origin", "This request was not accepted.");
  const csrf = validCsrf(request);
  if (csrf === null) return problem(403, "csrf_failed", "This request could not be verified.");

  const credential = credentialFromRequest(request);
  let revoked: "confirmed" | "already_invalid" | "unconfirmed" = "unconfirmed";
  if (credential === null) {
    revoked = "already_invalid";
  } else {
    try {
      const upstream = await backendFetch({ credential }, "/api/auth/logout", { method: "POST", csrf });
      if (upstream.status === 204) revoked = "confirmed";
      else if (upstream.status === 401) revoked = "already_invalid";
    } catch {
      revoked = "unconfirmed";
    }
  }

  const response = json({ revoked });
  clearSessionCookies(response);
  return response;
}
