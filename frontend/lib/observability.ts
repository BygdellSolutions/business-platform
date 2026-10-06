import "server-only";

import { AsyncLocalStorage } from "node:async_hooks";
import { randomBytes } from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

/**
 * The BFF's half of the logging and correlation policy (the backend's half is app/core/logging_config.py and
 * request_context.py; the two print the same JSON shape).
 *
 *  - ONE request id per BFF request, generated here. Whatever id the browser sends is ignored and replaced: the id is
 *    a correlation key (not a secret, not an authority) and must never carry attacker-chosen text into a log. The
 *    id is forwarded to FastAPI (`backendFetch`) so both services log the same value, and returned to the browser in
 *    `x-request-id` for support.
 *  - A log line is a short fixed event plus a few bounded fields: method, the path WITHOUT its query string, status,
 *    duration and the id. Never a header, cookie, body, token, secret or exception message (a fetch failure's message
 *    can name the backend host): only the error CLASS.
 *
 * This module is the one place in the BFF that writes logs.
 */

export const REQUEST_ID_HEADER = "x-request-id";
const SERVICE = "frontend";
const MAX_FIELD = 300;

const requestContext = new AsyncLocalStorage<{ requestId: string }>();

export function newRequestId(): string {
  return randomBytes(16).toString("hex");
}

/** The id of the BFF request being handled, or undefined outside a wrapped handler (a server-rendered page). */
export function currentRequestId(): string | undefined {
  return requestContext.getStore()?.requestId;
}

export type LogLevel = "info" | "warn" | "error";

export function logEvent(level: LogLevel, event: string, fields: Record<string, string | number | boolean | null | undefined> = {}): void {
  const record: Record<string, unknown> = { ts: new Date().toISOString(), level, service: SERVICE, event };
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined) record[key] = typeof value === "string" ? value.slice(0, MAX_FIELD) : value;
  }
  // JSON.stringify escapes control characters, so a value can never break the one-line-per-record format.
  const line = `${JSON.stringify(record)}\n`;
  (level === "info" ? process.stdout : process.stderr).write(line);
}

/** The class of an error, never its message. */
export function errorType(error: unknown): string {
  return error instanceof Error ? error.name.slice(0, 60) : typeof error;
}

/**
 * Wrap a route handler: give the request its id, run it, log the one safe line, and return the id. An unhandled error
 * is logged by class and answered with a fixed 500 (never the error text). The handler's own return type is kept.
 */
export function instrument<R extends Response>(handler: () => R | Promise<R>): (request: NextRequest) => Promise<R>;
export function instrument<Args extends [NextRequest, ...unknown[]], R extends Response>(handler: (...args: Args) => R | Promise<R>): (...args: Args) => Promise<R>;
export function instrument<R extends Response>(handler: (...args: never[]) => R | Promise<R>): (...args: [NextRequest, ...unknown[]]) => Promise<R> {
  return async (...args: [NextRequest, ...unknown[]]): Promise<R> => {
    const request = args[0];
    const requestId = newRequestId();
    const started = performance.now();
    let response: R;
    try {
      response = await requestContext.run({ requestId }, async () => (handler as (...a: unknown[]) => R | Promise<R>)(...args));
    } catch (error) {
      logEvent("error", "unhandled_error", { request_id: requestId, error_type: errorType(error) });
      response = NextResponse.json({ detail: "Internal error" }, { status: 500, headers: { "cache-control": "no-store" } }) as unknown as R;
    }
    response.headers.set(REQUEST_ID_HEADER, requestId);
    logEvent(response.status >= 500 ? "error" : "info", "request", {
      request_id: requestId,
      method: request.method,
      path: new URL(request.url).pathname, // the path only: the query string is never logged
      status: response.status,
      duration_ms: Math.round((performance.now() - started) * 10) / 10,
    });
    return response;
  };
}
