import type { NextRequest } from "next/server";

import { credentialFromRequest, loginPath } from "@/lib/auth/credential";
import { json, problem, readJsonObject, sessionModeOnly } from "@/lib/auth/handlers";
import { originProblem, validCsrf } from "@/lib/auth/request";
import { backendFetch, isUuid } from "@/lib/backend";
import { isInviteToken } from "@/lib/invite";
import { instrument } from "@/lib/observability";

/**
 * Accept an invitation as an EXISTING, signed-in account: an authenticated operation, so the ordinary session
 * rules apply (credential from the protected cookie; Origin against PUBLIC_ORIGIN; the CSRF double submit, which
 * FastAPI also checks against the session). The token travels only in the body. The organization is NOT taken from
 * the browser (no organization header is sent): FastAPI reads it from the invitation row, and only if the signed-in
 * account is the one the invitation was made for. Nothing the browser says about role or organization is forwarded.
 */
export const POST = instrument(async function POST(request: NextRequest) {
  const refused = sessionModeOnly();
  if (refused) return refused;
  if (originProblem(request) !== null) return problem(403, "forbidden_origin", "This request was not accepted.");

  const credential = credentialFromRequest(request);
  if (credential === null) return json({ detail: "Not authenticated", login: loginPath() }, 401);
  const csrf = validCsrf(request);
  if (csrf === null) return problem(403, "csrf_failed", "This request could not be verified. Reload the page and try again.");

  const body = await readJsonObject(request);
  if (body === null || !isInviteToken(body.token)) return problem(404, "invitation_unusable", "This invitation is invalid or has expired.");

  let upstream: Response;
  try {
    upstream = await backendFetch({ credential }, "/api/invite/accept", { method: "POST", body: JSON.stringify({ token: body.token }), csrf });
  } catch {
    return problem(502, "unavailable", "Invitations are unavailable right now.");
  }
  if (upstream.status === 401) return json({ detail: "Not authenticated", login: loginPath() }, 401);
  if (upstream.status === 404) return problem(404, "invitation_unusable", "This invitation is invalid or has expired.");
  if (upstream.status === 403) {
    const detail = ((await upstream.json().catch(() => undefined)) as { detail?: { code?: unknown } } | undefined)?.detail;
    return detail?.code === "invitation_wrong_account"
      ? problem(403, "invitation_wrong_account", "This invitation was made for a different account.")
      : problem(403, "csrf_failed", "This request could not be verified. Reload the page and try again.");
  }
  if (upstream.status !== 200) return problem(502, "unavailable", "Invitations are unavailable right now.");

  const data = (await upstream.json().catch(() => undefined)) as Record<string, unknown> | undefined;
  if (!data || typeof data.organization_id !== "string" || !isUuid(data.organization_id) || typeof data.role !== "string" || typeof data.joined !== "boolean") {
    return problem(502, "unavailable", "Invitations are unavailable right now.");
  }
  return json({ organization_id: data.organization_id, role: data.role, joined: data.joined });
});
