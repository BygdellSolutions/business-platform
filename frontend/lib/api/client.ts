import { networkError, normalizeError, type ApiResult } from "@/lib/api/errors";
import { CSRF_HEADER, readCsrfToken } from "@/lib/auth/cookies";

/**
 * The browser's only way to reach the backend: same-origin calls to the BFF at
 * /api/o/{orgId}/... . The organization is an explicit argument taken from the URL (never a
 * hidden global or a shared cookie), so two tabs can work in two organizations safely.
 * The browser never sets identity or organization headers; the BFF does.
 *
 * Never throws for HTTP or network failures: it returns an ApiResult.
 */

/**
 * Where an unauthenticated browser goes. `loginPath` is what the BFF named in its 401 (the login page of the
 * active mode); the page the user was on is kept as a RELATIVE return path (validated again by the login page
 * and by the BFF) and never for the login pages themselves, so this cannot loop.
 */
export function loginTarget(loginPath: string, here: { pathname: string; search: string }): string {
  if (!loginPath.startsWith("/") || loginPath.startsWith("//") || loginPath.includes("\\")) return "/login";
  if (loginPath !== "/login" || !here.pathname.startsWith("/o/")) return loginPath;
  return `/login?next=${encodeURIComponent(here.pathname + here.search)}`;
}

let onUnauthorized: (loginPath: string) => void = (loginPath) => {
  // A full page load (not a client transition) on purpose: no state from the signed-out
  // session may survive. The Next.js rule below recommends client navigation; we do not want it.
  if (typeof window !== "undefined") window.location.href = loginTarget(loginPath, window.location);
};

/** Replace what happens on a 401 (tests, or real authentication later). */
export function setUnauthorizedHandler(handler: (loginPath: string) => void): void {
  onUnauthorized = handler;
}

function loginPathOf(body: unknown): string {
  const login = typeof body === "object" && body !== null ? (body as { login?: unknown }).login : undefined;
  return typeof login === "string" ? login : "/dev-login";
}

export interface ApiRequest {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** JSON body. Money, VAT, quantity and decimal values must already be strings. */
  body?: unknown;
  /** The version of the record this change is based on (sent as If-Match). */
  ifMatch?: number;
  /** A client-generated retry key (organization creation only): see `app/api/organizations/route.ts`. */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export function bffPath(orgId: string, path: string): string {
  if (!path.startsWith("/")) throw new Error("path must start with /");
  return `/api/o/${encodeURIComponent(orgId)}${path}`;
}

/**
 * The CSRF double-submit header for a state-changing request: the page echoes the readable CSRF cookie. It
 * proves nothing by itself (the BFF compares it with the cookie and FastAPI with the session's stored hash).
 * There is no such cookie in development mode, so nothing is sent then.
 */
function csrfHeader(method: string | undefined): Record<string, string> {
  if (method === undefined || method === "GET" || typeof document === "undefined") return {};
  const token = readCsrfToken(document.cookie);
  return token === null ? {} : { [CSRF_HEADER]: token };
}

export async function apiFetch<T>(orgId: string, path: string, request: ApiRequest = {}): Promise<ApiResult<T>> {
  return send<T>(bffPath(orgId, path), request);
}

/**
 * A request that belongs to no organization (creating one): same-origin to the BFF's `/api/organizations`, with
 * the same CSRF echo, the same session-loss handling and the same result shape. There is no organization
 * argument to forget or to forge: the backend takes the owner from the authenticated user.
 */
export async function apiFetchAccount<T>(request: ApiRequest): Promise<ApiResult<T>> {
  return send<T>("/api/organizations", request);
}

async function send<T>(url: string, request: ApiRequest): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: request.method ?? "GET",
      headers: {
        accept: "application/json",
        ...(request.body === undefined ? {} : { "content-type": "application/json" }),
        ...(request.ifMatch === undefined ? {} : { "if-match": `"${request.ifMatch}"` }),
        ...(request.idempotencyKey === undefined ? {} : { "idempotency-key": request.idempotencyKey }),
        ...csrfHeader(request.method),
      },
      body: request.body === undefined ? undefined : JSON.stringify(request.body),
      credentials: "same-origin",
      cache: "no-store",
      signal: request.signal,
    });
  } catch (error) {
    if (request.signal?.aborted) throw error; // a cancelled request is not an error to display
    return { ok: false, error: networkError() };
  }

  const text = response.status === 204 ? "" : await response.text();
  let body: unknown = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = undefined;
    }
  }

  if (response.ok) return { ok: true, status: response.status, data: body as T };
  const error = normalizeError(response.status, body);
  if (error.kind === "unauthorized") onUnauthorized(loginPathOf(body));
  return { ok: false, error };
}

export interface DownloadedFile {
  blob: Blob;
  filename: string;
}

const PDF_FILENAME = /^invoice(?:-[A-Za-z0-9._-]{1,60})?\.pdf$/;

/** The filename from the BFF's Content-Disposition, only if it has the one expected shape. */
export function downloadFilename(disposition: string | null): string {
  const found = /^attachment;\s*filename="([^"\\\r\n]*)"$/.exec(disposition ?? "");
  return found && PDF_FILENAME.test(found[1]) ? found[1] : "invoice.pdf";
}

/**
 * Fetch an invoice PDF through the BFF as a Blob. Never throws for HTTP or network failures.
 * A 200 is accepted only if it really is a PDF response; anything else is treated as a failed
 * request (the BFF has already refused to pass along a response that is not the PDF).
 */
export async function apiDownloadPdf(orgId: string, path: string, signal?: AbortSignal): Promise<ApiResult<DownloadedFile>> {
  let response: Response;
  try {
    response = await fetch(bffPath(orgId, path), { method: "GET", headers: { accept: "application/pdf, application/json;q=0.9" }, credentials: "same-origin", cache: "no-store", signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    return { ok: false, error: networkError() };
  }

  if (response.ok) {
    const type = (response.headers.get("content-type") ?? "").toLowerCase().trim();
    if (type !== "application/pdf") return { ok: false, error: normalizeError(502, undefined) };
    return { ok: true, status: response.status, data: { blob: await response.blob(), filename: downloadFilename(response.headers.get("content-disposition")) } };
  }

  let body: unknown = undefined;
  try {
    body = JSON.parse(await response.text());
  } catch {
    body = undefined;
  }
  const error = normalizeError(response.status, body);
  if (error.kind === "unauthorized") onUnauthorized(loginPathOf(body));
  return { ok: false, error };
}
