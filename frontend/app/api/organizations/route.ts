import { NextResponse, type NextRequest } from "next/server";

import { authMode } from "@/lib/auth/config";
import { credentialFromRequest, loginPath } from "@/lib/auth/credential";
import { originProblem, validCsrf } from "@/lib/auth/request";
import { backendFetch, isRequestKey } from "@/lib/backend";
import { isSameOrigin } from "@/lib/origin";

/**
 * Organization creation: the one BFF route that is NOT scoped to an organization (there is none yet).
 *
 *   POST /api/organizations   ->   FastAPI POST /api/organizations
 *
 * It is a separate route, not a new area of the `/api/o/{orgId}/...` catch-all, so the organization-scoped door
 * keeps meaning "this organization" and this door can never be given an organization or an owner. As in the
 * scoped door, upstream headers are built from scratch: the credential comes from the protected cookie, never
 * from the request, and nothing names a user, owner, role or organization. The browser's body is relayed as
 * JSON text (FastAPI refuses unknown fields such as an owner); the only client header forwarded is the retry key
 * (`Idempotency-Key`, validated by shape, scoped to the creator by FastAPI). Session mode applies the same
 * Origin and CSRF double-submit checks as every other state-changing request; FastAPI independently checks the
 * CSRF token against the session and judges `can_create_organizations` itself.
 */

const MAX_BODY_BYTES = 16_000;
const NO_STORE = { "cache-control": "no-store" };

function failure(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const mode = authMode();
  if (mode === "none") return failure(503, "Authentication is not configured");
  if (mode === "session" ? originProblem(request) !== null : !isSameOrigin(request)) return failure(403, "Cross-origin requests are not allowed");

  const credential = credentialFromRequest(request);
  if (credential === null) return NextResponse.json({ detail: "Not authenticated", login: loginPath() }, { status: 401, headers: NO_STORE });

  let csrf: string | undefined;
  if (mode === "session") {
    const valid = validCsrf(request);
    if (valid === null) return failure(403, "CSRF validation failed");
    csrf = valid;
  }

  const keyHeader = request.headers.get("idempotency-key");
  if (keyHeader !== null && !isRequestKey(keyHeader)) return failure(400, "Invalid Idempotency-Key header");

  const declared = Number.parseInt(request.headers.get("content-length") ?? "0", 10);
  if (declared > MAX_BODY_BYTES) return failure(413, "Request body too large");
  const body = await request.text();
  if (body.length > MAX_BODY_BYTES) return failure(413, "Request body too large");
  if (!(request.headers.get("content-type")?.toLowerCase() ?? "").startsWith("application/json")) return failure(415, "Only JSON bodies are accepted");

  let upstream: Response;
  try {
    upstream = await backendFetch({ credential }, "/api/organizations", { method: "POST", body, csrf, idempotencyKey: keyHeader ?? undefined });
  } catch {
    return failure(502, "Backend unavailable");
  }
  if (upstream.status >= 300 && upstream.status < 400) return failure(502, "Unexpected response from the backend");
  if (upstream.status === 401) return NextResponse.json({ detail: "Not authenticated", login: loginPath() }, { status: 401, headers: NO_STORE });

  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", ...NO_STORE },
  });
}
