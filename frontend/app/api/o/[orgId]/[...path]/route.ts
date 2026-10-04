import { NextResponse, type NextRequest } from "next/server";

import { apiPathFromSegments, backendFetch, isInvoicePdfPath, isUuid, parseIfMatch } from "@/lib/backend";
import { DEV_USER_COOKIE, identityFromCookie } from "@/lib/identity";
import { isSameOrigin } from "@/lib/origin";
import { pdfPassThrough, unexpected } from "@/lib/pdf-response";

/**
 * The BFF: the browser's only door to FastAPI.
 *
 *   /api/o/{orgId}/customers?limit=5   ->   FastAPI GET /api/customers?limit=5
 *
 * What it does, and what it deliberately does not:
 *  - the dev identity comes from the httpOnly cookie and the organization from the URL; both
 *    are turned into X-Dev-User-Email / X-Organization-Id by `backendFetch`, which builds the
 *    request headers from scratch. Any identity or organization header, cookie or credential
 *    the client sends is ignored.
 *  - it validates SHAPES only (a UUID, a known API area, safe path segments, same origin,
 *    JSON bodies). It does NOT decide whether the user may use that organization: FastAPI
 *    independently verifies the membership on every scoped request and answers 404 otherwise.
 *  - responses carry only the status and body; no headers or cookies from the backend. The one
 *    exception in KIND is the binary PDF of an invoice (see lib/pdf-response.ts): it is validated, and
 *    its headers are rebuilt, never copied.
 *  - requests carry no client headers EXCEPT a validated If-Match (optimistic concurrency).
 */

type Context = { params: Promise<{ orgId: string; path: string[] }> };

const MAX_BODY_BYTES = 1_000_000;
const NO_STORE = { "cache-control": "no-store" };

function failure(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
}

async function handle(request: NextRequest, context: Context): Promise<NextResponse> {
  const { orgId, path } = await context.params;

  if (!isUuid(orgId)) return failure(404, "Not found");
  const apiPath = apiPathFromSegments(path);
  if (apiPath === null) return failure(404, "Not found");

  const method = request.method as "GET" | "POST" | "PATCH" | "DELETE";
  if (method !== "GET" && !isSameOrigin(request)) return failure(403, "Cross-origin requests are not allowed");

  const email = identityFromCookie(request.cookies.get(DEV_USER_COOKIE)?.value);
  if (email === null) return failure(401, "Not authenticated");

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
    upstream = await backendFetch({ email, orgId }, apiPath, {
      method,
      search: request.nextUrl.search,
      body,
      ifMatch: ifMatch ?? undefined,
      accept: wantsPdf ? "application/pdf, application/json;q=0.9" : undefined,
    });
  } catch {
    return failure(502, "Backend unavailable");
  }
  if (upstream.status >= 300 && upstream.status < 400) return failure(502, "Unexpected response from the backend");

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
export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const DELETE = handle;
