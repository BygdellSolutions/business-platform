import "server-only";

import { NextResponse } from "next/server";

import { currentRequestId, errorType, logEvent } from "@/lib/observability";

/**
 * The failure contract between the BFF and the browser when FastAPI cannot do its job.
 *
 * What the browser may learn is FIXED and coarse: the backend is unavailable (502/503) or too slow (504), with a
 * stable machine-readable code. It never learns the backend's hostname, a stack trace, a connection string, a driver
 * message or a raw fetch error: those are logged by CLASS only. And an ordinary application answer (a 4xx: not found,
 * forbidden, conflict, validation) is NOT an infrastructure failure and passes through untouched: only a 5xx, or the
 * backend refusing the BFF's own internal secret (a deployment misconfiguration), is replaced.
 */

const NO_STORE = { "cache-control": "no-store" };
export const INTERNAL_AUTH_REFUSED_HEADER = "x-internal-auth";

export type UpstreamFailureKind = "timeout" | "unreachable" | "bad_status" | "internal_auth";

function body(code: string) {
  return { detail: "Backend unavailable", code };
}

/** A thrown fetch failure: refused connection, DNS failure, reset, timeout. */
export function classifyFetchError(error: unknown): "timeout" | "unreachable" {
  const name = error instanceof Error ? error.name : "";
  return name === "TimeoutError" || name === "AbortError" ? "timeout" : "unreachable";
}

export function upstreamUnavailable(error: unknown): NextResponse {
  const kind = classifyFetchError(error);
  logEvent("error", "upstream_failed", { request_id: currentRequestId(), kind, error_type: errorType(error) });
  return kind === "timeout"
    ? NextResponse.json(body("upstream_timeout"), { status: 504, headers: NO_STORE })
    : NextResponse.json(body("upstream_unavailable"), { status: 502, headers: NO_STORE });
}

/** An answer FastAPI gave that means "the service is not working", not "your request was not acceptable". */
export function isInfrastructureFailure(upstream: Response): boolean {
  return upstream.status >= 500 || upstream.headers.get(INTERNAL_AUTH_REFUSED_HEADER) === "rejected";
}

export function infrastructureFailure(upstream: Response): NextResponse {
  const internal = upstream.headers.get(INTERNAL_AUTH_REFUSED_HEADER) === "rejected";
  // The refusal of our own secret is OUR misconfiguration: log it loudly, say nothing about it to the browser.
  logEvent("error", internal ? "upstream_refused_internal_secret" : "upstream_error_status", { request_id: currentRequestId(), status: upstream.status });
  return upstream.status === 503
    ? NextResponse.json(body("upstream_unavailable"), { status: 503, headers: { ...NO_STORE, "retry-after": "5" } })
    : NextResponse.json(body("upstream_unavailable"), { status: 502, headers: NO_STORE });
}
