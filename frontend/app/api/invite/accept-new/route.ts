import type { NextRequest } from "next/server";

import { callAuthEndpoint, parseSession, preAuthProblem, problem, readJsonObject, retryAfter, sessionModeOnly, signedIn } from "@/lib/auth/handlers";
import { isUuid } from "@/lib/backend";
import { isInviteToken } from "@/lib/invite";

/**
 * Create the account an invitation was made for, BEFORE authentication (the invitee has no session): the invite
 * page sends the token, a name and a password in this body only, with the pre-auth double submit (checked here
 * before FastAPI is contacted). There is no email, organization or role field: the email comes from the invitation
 * and the organization and role from its locked row. FastAPI creates user, credential, membership and session in
 * one transaction; on success the BFF sets the protected cookies exactly as for a login and says where to go.
 */
export async function POST(request: NextRequest) {
  const refused = sessionModeOnly() ?? preAuthProblem(request);
  if (refused) return refused;

  const body = await readJsonObject(request);
  if (body === null || typeof body.name !== "string" || typeof body.password !== "string" || body.name.length > 255 || body.password.length > 4096) {
    return problem(400, "invalid_request", "This request could not be understood.");
  }
  if (!isInviteToken(body.token)) return problem(404, "invitation_unusable", "This invitation is invalid or has expired.");

  let upstream: Response;
  try {
    upstream = await callAuthEndpoint(request, "/api/invite/accept-new", { token: body.token, name: body.name, password: body.password });
  } catch {
    return problem(502, "unavailable", "Creating the account is unavailable right now.");
  }

  if (upstream.status === 200) {
    const data = (await upstream.json().catch(() => undefined)) as Record<string, unknown> | undefined;
    const session = parseSession(data);
    const organizationId = data?.organization_id;
    if (session === null || typeof organizationId !== "string" || !isUuid(organizationId)) return problem(502, "unavailable", "Creating the account is unavailable right now.");
    return signedIn(session, `/o/${organizationId}`);
  }
  if (upstream.status === 404) return problem(404, "invitation_unusable", "This invitation is invalid or has expired.");
  if (upstream.status === 409) return problem(409, "account_exists", "An account with this email already exists. Sign in to accept the invitation.");
  if (upstream.status === 422) {
    // The backend's own password-policy wording is safe to show (it never contains a secret).
    const detail = ((await upstream.json().catch(() => undefined)) as { detail?: { message?: unknown } } | undefined)?.detail;
    const message = typeof detail?.message === "string" && detail.message.length <= 200 ? detail.message : "That password is not acceptable.";
    return problem(422, "password_policy", message);
  }
  if (upstream.status === 429) return problem(429, "throttled", "Too many attempts. Try again later.", retryAfter(upstream));
  if (upstream.status === 503) return problem(503, "busy", "The service is busy. Try again in a moment.", retryAfter(upstream));
  return problem(502, "unavailable", "Creating the account is unavailable right now.");
}
