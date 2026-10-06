import { NextResponse, type NextRequest } from "next/server";

import { apiPathFromSegments, backendFetch, isInvoicePdfPath, isUuid, parseIfMatch } from "@/lib/backend";
import { authMode } from "@/lib/auth/config";
import { credentialFromRequest, loginPath } from "@/lib/auth/credential";
import { originProblem, validCsrf } from "@/lib/auth/request";
import { isSameOrigin } from "@/lib/origin";
import { instrument } from "@/lib/observability";
import { infrastructureFailure, isInfrastructureFailure, upstreamUnavailable } from "@/lib/upstream";
import { pdfPassThrough, unexpected } from "@/lib/pdf-response";

/**
 * The BFF: the browser's only door to FastAPI.
 *
 *   /api/o/{orgId}/customers?limit=5   ->   FastAPI GET /api/customers?limit=5
 *
 * What it does, and what it deliberately does not:
 *  - the credential comes from the protected httpOnly cookie (dev: the dev-user cookie, session: the opaque
 *    session token) and the organization from the URL; both are turned into upstream headers by `backendFetch`,
 *    which builds them from scratch (`Authorization: Bearer <token>` or, in development, `X-Dev-User-Email`, and
 *    `X-Organization-Id`). Any Authorization, Cookie, identity, organization, role or proxy header the client
 *    sends is ignored, never forwarded.
 *  - session mode, state-changing requests: the browser's Origin must equal PUBLIC_ORIGIN (a missing one is
 *    refused; Host is not consulted) and the X-CSRF-Token header must equal the readable CSRF cookie; only then is
 *    the token forwarded, and FastAPI independently checks it against the session's stored hash.
 *  - a 401 from FastAPI (expired, revoked, disabled) is answered with a fixed body naming the login page; a 403
 *    (role, CSRF) is NOT an authentication failure and is relayed as is.
 *  - it validates SHAPES only (a UUID, a known API area, safe path segments, same origin,
 *    JSON bodies). It does NOT decide whether the user may use that organization: FastAPI
 *    independently verifies the membership on every scoped request and answers 404 otherwise.
 *  - responses carry only the status and body; no headers or cookies from the backend. The one
 *    exception in KIND is the binary PDF of an invoice (see lib/pdf-response.ts): it is validated, and
 *    its headers are rebuilt, never copied.
 *  - requests carry no client headers EXCEPT a validated If-Match (optimistic concurrency). The internal secret and the
 *    request id are added by `backendFetch` from the BFF's own state; the browser cannot supply either.
 *  - when FastAPI cannot answer (refused connection, timeout, any 5xx, or it refusing our internal secret) the browser gets
 *    a fixed, coarse 502/503/504 (see lib/upstream.ts); ordinary 4xx answers pass through untouched.
 */

type Context = { params: Promise<{ orgId: string; path: string[] }> };

const MAX_BODY_BYTES = 1_000_000;
const NO_STORE = { "cache-control": "no-store" };

function failure(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
}

/** The one answer for "sign in again": a fixed body (nothing from the backend) naming where to go. */
function unauthenticated(): NextResponse {
  return NextResponse.json({ detail: "Not authenticated", login: loginPath() }, { status: 401, headers: NO_STORE });
}

async function handle(request: NextRequest, context: Context): Promise<NextResponse> {
  const { orgId, path } = await context.params;

  if (!isUuid(orgId)) return failure(404, "Not found");
  const apiPath = apiPathFromSegments(path);
  if (apiPath === null) return failure(404, "Not found");

  const method = request.method as "GET" | "POST" | "PATCH" | "DELETE";
  const mode = authMode();
  if (mode === "none") return failure(503, "Authentication is not configured");
  if (method !== "GET") {
    const refused = mode === "session" ? originProblem(request) !== null : !isSameOrigin(request);
    if (refused) return failure(403, "Cross-origin requests are not allowed");
  }

  const credential = credentialFromRequest(request);
  if (credential === null) return unauthenticated();

  // Session mode: the double-submit half of CSRF, checked HERE (FastAPI checks the session-bound half).
  let csrf: string | undefined;
  if (method !== "GET" && mode === "session") {
    const valid = validCsrf(request);
    if (valid === null) return failure(403, "CSRF validation failed");
    csrf = valid;
  }

  // The one client header that is forwarded: the version a change is based on. Anything but a
  // plain integer is refused here; the backend still decides whether it is current.
  const ifMatch = parseIfMatch(request.headers.get("if-match"));
  if (ifMatch === "invalid") return failure(400, "Invalid If-Match header");

  let body: string | undefined;
  if (method !== "GET") {
    const declared = Number.parseInt(request.headers.get("content-length") ?? "0", 10);
    if (declared > MAX_BODY_BYTES) return failure(413, "Request body too large");
    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) return failure(413, "Request body too large");
    if (text.length > 0) {
      const type = request.headers.get("content-type")?.toLowerCase() ?? "";
      if (!type.startsWith("application/json")) return failure(415, "Only JSON bodies are accepted");
      body = text;
    }
  }

  // The one binary resource: an invoice's frozen PDF, GET only (a body or query on it is not part of the contract).
  const wantsPdf = isInvoicePdfPath(apiPath);
  if (wantsPdf && (method !== "GET" || request.nextUrl.search !== "")) return failure(404, "Not found");

  let upstream: Response;
  try {
    upstream = await backendFetch({ credential, orgId }, apiPath, {
      method,
      search: request.nextUrl.search,
      body,
      ifMatch: ifMatch ?? undefined,
      csrf,
      accept: wantsPdf ? "application/pdf, application/json;q=0.9" : undefined,
    });
  } catch (error) {
    return upstreamUnavailable(error);
  }
  if (upstream.status >= 300 && upstream.status < 400) return failure(502, "Unexpected response from the backend");
  // A 5xx, or FastAPI refusing the BFF's own secret, is replaced by a fixed answer; ordinary 4xx pass through below.
  if (isInfrastructureFailure(upstream)) return infrastructureFailure(upstream);
  if (upstream.status === 401) return unauthenticated();

  const upstreamType = (upstream.headers.get("content-type") ?? "").toLowerCase();
  if (wantsPdf && upstream.status === 200) return pdfPassThrough(upstream);
  // Everything else is relayed as JSON text. A PDF (or any non-JSON body) that arrives anywhere but the one
  // validated path above is never passed along: it would be corrupted as text, and it is not what we asked for.
  if (upstream.status !== 204 && upstream.status !== 205 && !upstreamType.startsWith("application/json")) {
    if (wantsPdf || upstreamType.startsWith("application/pdf")) return unexpected();
  }

  const bodyless = upstream.status === 204 || upstream.status === 205;
  return new NextResponse(bodyless ? null : await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", ...NO_STORE },
  });
}

// Only these methods exist; everything else is answered 405 by Next.js.
export const GET = instrument(handle);
export const POST = instrument(handle);
export const PATCH = instrument(handle);
export const DELETE = instrument(handle);
