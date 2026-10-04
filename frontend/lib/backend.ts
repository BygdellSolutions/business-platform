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

import type { Credential } from "@/lib/auth/credential";
import { UUID, isUuid } from "@/lib/uuid";

const DEFAULT_BACKEND_URL = "http://localhost:8000";
const TIMEOUT_MS = 15_000;

export { UUID, isUuid };

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
  "invoices",
  "invoiceable-transactions",
  "organization",
  "members",
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
  /**
   * Who is calling, as the server holds it (see `lib/auth/credential`). Null only for the few operations that
   * happen BEFORE a session exists (login, setup-link redemption).
   */
  credential: Credential | null;
  /** The organization the request is scoped to (X-Organization-Id), if any. */
  orgId?: string;
}

/**
 * The version a change is based on (optimistic concurrency, see the backend's
 * app/modules/sales/versioning.py), as it may travel in an If-Match header: a quoted or bare
 * integer. Returns the normalized quoted form, null if there is none, or "invalid". It is the
 * ONLY client-supplied header the BFF forwards; it carries a number and no authority.
 */
export function parseIfMatch(value: string | null | undefined): string | null | "invalid" {
  if (value === null || value === undefined) return null;
  const found = /^"?(\d{1,9})"?$/.exec(value.trim());
  return found ? `"${found[1]}"` : "invalid";
}

export interface HeaderOptions {
  json?: boolean;
  ifMatch?: string;
  accept?: string;
  /** A CSRF token the BFF has ALREADY validated against the browser's cookie (session mode, mutations only). */
  csrf?: string;
  /** The client address the BFF itself established (login and setup only); see `clientAddress`. */
  clientAddress?: string;
  /** A client-generated retry key, already validated by `isRequestKey` (organization creation only). */
  idempotencyKey?: string;
}

/** The shape of an `Idempotency-Key`: 32 random bytes, base64url without padding (43 characters). */
export function isRequestKey(value: string | null | undefined): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{43}$/.test(value);
}

/**
 * Headers for a backend request, built from scratch: nothing the client sent is copied. The only values are
 * the credential (as `Authorization: Bearer` or, in development, `X-Dev-User-Email`), the organization taken
 * from the URL, and the few validated extras above.
 */
export function buildBackendHeaders(identity: BackendIdentity, options: HeaderOptions = {}): Headers {
  const headers = new Headers({ accept: options.accept ?? "application/json" });
  const { credential } = identity;
  if (credential?.kind === "dev") headers.set("x-dev-user-email", credential.email);
  if (credential?.kind === "session") headers.set("authorization", `Bearer ${credential.token}`);
  if (identity.orgId) headers.set("x-organization-id", identity.orgId);
  if (options.json) headers.set("content-type", "application/json");
  if (options.ifMatch) headers.set("if-match", options.ifMatch);
  if (options.csrf) headers.set("x-csrf-token", options.csrf);
  if (options.clientAddress) headers.set("x-client-ip", options.clientAddress);
  if (options.idempotencyKey) headers.set("idempotency-key", options.idempotencyKey);
  return headers;
}

export interface BackendRequest {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** Query string including the leading "?", or "". */
  search?: string;
  /** A JSON body as text. */
  body?: string;
  /** A normalized If-Match value (see parseIfMatch). */
  ifMatch?: string;
  /** The media type to ask for; JSON unless the BFF is fetching the one binary resource it passes through. */
  accept?: string;
  csrf?: string;
  clientAddress?: string;
  idempotencyKey?: string;
}

/**
 * The ONE binary resource the BFF passes through: the frozen PDF of an invoice. Everything else it
 * relays is JSON text.
 */
const INVOICE_PDF_PATH = /^\/api\/invoices\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/pdf$/i;

export function isInvoicePdfPath(apiPath: string): boolean {
  return INVOICE_PDF_PATH.test(apiPath);
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
    headers: buildBackendHeaders(identity, { json: request.body !== undefined, ifMatch: request.ifMatch, accept: request.accept, csrf: request.csrf, clientAddress: request.clientAddress, idempotencyKey: request.idempotencyKey }),
    body: request.body,
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
}
