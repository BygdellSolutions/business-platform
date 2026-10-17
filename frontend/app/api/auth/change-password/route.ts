import type { NextRequest } from "next/server";

import { credentialFromRequest } from "@/lib/auth/credential";
import { json, problem, readJsonObject, retryAfter, sessionModeOnly } from "@/lib/auth/handlers";
import { originProblem, validCsrf } from "@/lib/auth/request";
import { backendFetch } from "@/lib/backend";
import { instrument } from "@/lib/observability";

/**
 * Change the signed-in user's own password: a CSRF-protected state change, like logout. FastAPI checks the
 * current password and the password policy, and ends every OTHER session of the user; this browser's session
 * stays. Only FastAPI's own answers are passed on (a fixed set of codes), never upstream details.
 */
export const POST = instrument(async function POST(request: NextRequest) {
  const refused = sessionModeOnly();
  if (refused) return refused;
  if (originProblem(request) !== null) return problem(403, "forbidden_origin", "This request was not accepted.");
  const csrf = validCsrf(request);
  if (csrf === null) return problem(403, "csrf_failed", "This request could not be verified.");
  const credential = credentialFromRequest(request);
  if (credential === null) return problem(401, "unauthenticated", "Sign in again.");

  const body = await readJsonObject(request);
  if (body === null || typeof body.current_password !== "string" || typeof body.new_password !== "string" || body.current_password.length > 4096 || body.new_password.length > 4096) {
    return problem(400, "invalid_request", "Enter your current and your new password.");
  }

  let upstream: Response;
  try {
    upstream = await backendFetch({ credential }, "/api/auth/change-password", {
      method: "POST",
      csrf,
      body: JSON.stringify({ current_password: body.current_password, new_password: body.new_password }),
    });
  } catch {
    return problem(502, "unavailable", "Changing the password is unavailable right now.");
  }

  if (upstream.status === 204) return json({ changed: true });
  if (upstream.status === 401) return problem(401, "unauthenticated", "Sign in again.");
  if (upstream.status === 400) return problem(400, "wrong_current_password", "The current password is not correct.");
  if (upstream.status === 422) {
    const detail = ((await upstream.json().catch(() => null)) as { detail?: { message?: unknown } } | null)?.detail;
    const message = typeof detail?.message === "string" ? detail.message : "The new password does not meet the password rules.";
    return problem(422, "password_policy", message);
  }
  if (upstream.status === 429) return problem(429, "throttled", "Too many attempts. Try again later.", retryAfter(upstream));
  if (upstream.status === 503) return problem(503, "busy", "The service is busy. Try again in a moment.", retryAfter(upstream));
  return problem(502, "unavailable", "Changing the password is unavailable right now.");
});
