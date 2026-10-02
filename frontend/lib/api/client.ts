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
      headers: request.body === undefined ? { accept: "application/json" } : { accept: "application/json", "content-type": "application/json" },
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
