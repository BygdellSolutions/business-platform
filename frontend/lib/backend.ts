import "server-only";

/**
 * The ONLY module that talks to FastAPI.
 *
 * Browsers never call FastAPI and never send identity or organization headers. Server
 * components and the BFF route handler call `backendFetch`, which builds the request headers
 * from scratch: nothing the client sent is copied, and `X-Dev-User-Email` / `X-Organization-Id`
 * are added here and nowhere else. FastAPI stays the authority: it independently verifies
 * the user and their membership in the organization on every scoped request; the header is
 * only a selector.
 */

const DEFAULT_BACKEND_URL = "http://localhost:8000";
const TIMEOUT_MS = 15_000;

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string | undefined | null): value is string {
  return typeof value === "string" && UUID.test(value);
}

export function backendUrl(): string {
  return (process.env.BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

/** Areas of the FastAPI app the browser may reach through the BFF. */
export const ALLOWED_API_AREAS = [
  "customers",
  "items",
  "horses",
  "transactions",
  "custom-fields",
  "me",
] as const;

const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

/**
 * `/api/...` path for the catch-all segments, or null if anything is off. Segments may
 * contain only letters, digits, `_` and `-`, so `..`, `.`, slashes, backslashes, percent
 * escapes and empty segments can never reach the backend, and the first segment must be a
 * known API area.
 */
export function apiPathFromSegments(segments: string[] | undefined): string | null {
  if (!segments || segments.length === 0 || segments.length > 6) return null;
  if (!segments.every((segment) => SEGMENT.test(segment))) return null;
  if (!(ALLOWED_API_AREAS as readonly string[]).includes(segments[0])) return null;
  return `/api/${segments.join("/")}`;
}

export interface BackendIdentity {
  /** The dev user's email (X-Dev-User-Email). */
  email: string;
  /** The organization the request is scoped to (X-Organization-Id), if any. */
  orgId?: string;
}

/** Headers for a backend request, built from scratch (never from client headers). */
export function buildBackendHeaders(identity: BackendIdentity, options: { json?: boolean } = {}): Headers {
  const headers = new Headers({ accept: "application/json", "x-dev-user-email": identity.email });
  if (identity.orgId) headers.set("x-organization-id", identity.orgId);
  if (options.json) headers.set("content-type", "application/json");
  return headers;
}

export interface BackendRequest {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** Query string including the leading "?", or "". */
  search?: string;
  /** A JSON body as text. */
  body?: string;
}

export async function backendFetch(
  identity: BackendIdentity,
  path: string,
  request: BackendRequest = {},
): Promise<Response> {
  if (!path.startsWith("/api/") || path.includes("..") || path.includes("//")) {
    throw new Error(`refusing to call a non-API path: ${path}`);
  }
  if (identity.orgId !== undefined && !isUuid(identity.orgId)) {
    throw new Error("refusing to call the backend with a malformed organization id");
  }
  return fetch(`${backendUrl()}${path}${request.search ?? ""}`, {
    method: request.method ?? "GET",
    headers: buildBackendHeaders(identity, { json: request.body !== undefined }),
    body: request.body,
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
}
