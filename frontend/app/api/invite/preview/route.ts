import type { NextRequest } from "next/server";

import { json, preAuthProblem, problem, readJsonObject, sessionModeOnly } from "@/lib/auth/handlers";
import { backendFetch } from "@/lib/backend";
import { isInviteToken } from "@/lib/invite";

/**
 * Invitation preview, BEFORE authentication: the invite page posts the token (never in a URL) with the pre-auth
 * double submit, checked here before FastAPI is contacted. FastAPI answers with only what the acceptance screen
 * needs; every unusable token (unknown, revoked, expired, used) gets the one generic answer. The pre-auth secret
 * is never forwarded and the browser's credentials are not used (this is not an authenticated operation).
 */
export async function POST(request: NextRequest) {
  const refused = sessionModeOnly() ?? preAuthProblem(request);
  if (refused) return refused;

  const body = await readJsonObject(request);
  if (body === null || !isInviteToken(body.token)) return problem(404, "invitation_unusable", "This invitation is invalid or has expired.");

  let upstream: Response;
  try {
    upstream = await backendFetch({ credential: null }, "/api/invite/preview", { method: "POST", body: JSON.stringify({ token: body.token }) });
  } catch {
    return problem(502, "unavailable", "Invitations are unavailable right now.");
  }
  if (upstream.status === 404) return problem(404, "invitation_unusable", "This invitation is invalid or has expired.");
  if (upstream.status !== 200) return problem(502, "unavailable", "Invitations are unavailable right now.");

  const data = (await upstream.json().catch(() => undefined)) as Record<string, unknown> | undefined;
  if (!data || typeof data.organization_name !== "string" || typeof data.email !== "string" || typeof data.role !== "string" || typeof data.account_exists !== "boolean") {
    return problem(502, "unavailable", "Invitations are unavailable right now.");
  }
  // Rebuilt field by field: nothing else FastAPI says is passed on.
  return json({ organization_name: data.organization_name, email: data.email, role: data.role, account_exists: data.account_exists });
}
