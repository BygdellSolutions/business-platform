import type { NextRequest } from "next/server";

import { isToken } from "@/lib/auth/cookies";
import { callAuthEndpoint, parseSession, preAuthProblem, problem, readJsonObject, retryAfter, sessionModeOnly, signedIn } from "@/lib/auth/handlers";
import { instrument } from "@/lib/observability";

/**
 * Redeem a single-use setup link (the first user, or recovery): the setup page sends the link secret ONLY in
 * this request body, with the pre-auth header. FastAPI's S1 contract is that a successful redemption sets the
 * password and starts a session, so the BFF sets the protected cookies exactly as for a login. An unknown, used,
 * expired or revoked link gets one generic answer.
 */
export const POST = instrument(async function POST(request: NextRequest) {
  const refused = sessionModeOnly() ?? preAuthProblem(request);
  if (refused) return refused;

  const body = await readJsonObject(request);
  if (body === null || typeof body.token !== "string" || typeof body.password !== "string" || body.password.length > 4096) {
    return problem(400, "invalid_request", "This request could not be understood.");
  }
  if (!isToken(body.token)) return problem(400, "invalid_setup_link", "This link is invalid or has expired.");

  let upstream: Response;
  try {
    upstream = await callAuthEndpoint(request, "/api/auth/setup", { token: body.token, password: body.password });
  } catch {
    return problem(502, "unavailable", "Setting a password is unavailable right now.");
  }

  if (upstream.status === 200) {
    const session = parseSession(await upstream.json().catch(() => undefined));
    return session === null ? problem(502, "unavailable", "Setting a password is unavailable right now.") : signedIn(session, "/");
  }
  if (upstream.status === 400) return problem(400, "invalid_setup_link", "This link is invalid or has expired.");
  if (upstream.status === 422) {
    // The backend's own password-policy wording is safe to show (it never contains a secret).
    const detail = ((await upstream.json().catch(() => undefined)) as { detail?: { message?: unknown } } | undefined)?.detail;
    const message = typeof detail?.message === "string" && detail.message.length <= 200 ? detail.message : "That password is not acceptable.";
    return problem(422, "password_policy", message);
  }
  if (upstream.status === 429) return problem(429, "throttled", "Too many attempts. Try again later.", retryAfter(upstream));
  if (upstream.status === 503) return problem(503, "busy", "The service is busy. Try again in a moment.", retryAfter(upstream));
  return problem(502, "unavailable", "Setting a password is unavailable right now.");
});
