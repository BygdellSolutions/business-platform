import { networkError, normalizeError, type ApiResult } from "@/lib/api/errors";

/**
 * The browser's only way to reach the backend: same-origin calls to the BFF at
 * /api/o/{orgId}/... . The organization is an explicit argument taken from the URL (never a
 * hidden global or a shared cookie), so two tabs can work in two organizations safely.
 * The browser never sets identity or organization headers; the BFF does.
 *
 * Never throws for HTTP or network failures: it returns an ApiResult.
 */

let onUnauthorized: () => void = () => {
  // A full page load (not a client transition) on purpose: no state from the signed-out
  // session may survive. The Next.js rule below recommends client navigation; we do not want it.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  if (typeof window !== "undefined") window.location.href = "/dev-login";
};

/** Replace what happens on a 401 (tests, or real authentication later). */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

export interface ApiRequest {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** JSON body. Money, VAT, quantity and decimal values must already be strings. */
  body?: unknown;
  /** The version of the record this change is based on (sent as If-Match). */
  ifMatch?: number;
  signal?: AbortSignal;
}

export function bffPath(orgId: string, path: string): string {
  if (!path.startsWith("/")) throw new Error("path must start with /");
  return `/api/o/${encodeURIComponent(orgId)}${path}`;
}

export async function apiFetch<T>(orgId: string, path: string, request: ApiRequest = {}): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(bffPath(orgId, path), {
      method: request.method ?? "GET",
      headers: {
        accept: "application/json",
        ...(request.body === undefined ? {} : { "content-type": "application/json" }),
        ...(request.ifMatch === undefined ? {} : { "if-match": `"${request.ifMatch}"` }),
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
  if (error.kind === "unauthorized") onUnauthorized();
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
  if (error.kind === "unauthorized") onUnauthorized();
  return { ok: false, error };
}
