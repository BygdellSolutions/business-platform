import type { NextRequest } from "next/server";

import { callOwnAuth, json, problem } from "@/lib/auth/handlers";
import { clearSessionCookies } from "@/lib/auth/session-cookies";
import { instrument } from "@/lib/observability";

/**
 * Sign out everywhere, this browser included. Once FastAPI confirms, this browser's cookies are cleared too (with the attributes
 * they were set with); if it could not be confirmed nothing is cleared, so the person is never told they are signed out when
 * the server still honours the session.
 */
export const POST = instrument(async function POST(request: NextRequest) {
  const result = await callOwnAuth(request, "/api/auth/sessions/end-all", "POST");
  if ("refused" in result) return result.refused;
  const { upstream } = result;
  if (upstream.status === 401) {
    const response = problem(401, "unauthenticated", "Sign in again.");
    clearSessionCookies(response);
    return response;
  }
  const body = (await upstream.json().catch(() => null)) as { ended?: unknown } | null;
  if (upstream.status !== 200 || typeof body?.ended !== "number") return problem(502, "unavailable", "Signing out everywhere did not complete. Try again.");
  const response = json({ ended: body.ended });
  clearSessionCookies(response);
  return response;
});
