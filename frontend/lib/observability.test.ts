// @vitest-environment node
import { NextRequest, NextResponse } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { currentRequestId, errorType, instrument, logEvent, newRequestId } from "@/lib/observability";

const SENTINEL = "SENTINEL-SECRET-VALUE-9f2a";
let out: string[];
let err: string[];

beforeEach(() => {
  out = [];
  err = [];
  vi.spyOn(process.stdout, "write").mockImplementation(((chunk: string) => (out.push(String(chunk)), true)) as never);
  vi.spyOn(process.stderr, "write").mockImplementation(((chunk: string) => (err.push(String(chunk)), true)) as never);
});
afterEach(() => vi.restoreAllMocks());

const lines = () => [...out, ...err].join("").split("\n").filter(Boolean).map((line) => JSON.parse(line) as Record<string, unknown>);
const request = (path = "/api/x", headers: Record<string, string> = {}, method = "GET") => new NextRequest(`http://127.0.0.1:3100${path}`, { method, headers });

describe("the request id", () => {
  it("is a fresh 32-hex value per request and not derived from anything the browser sent", async () => {
    const seen: (string | undefined)[] = [];
    const handler = instrument(async () => (seen.push(currentRequestId()), NextResponse.json({})));
    const a = await handler(request("/api/x", { "x-request-id": "browser-chosen-id-1234567890" }));
    const b = await handler(request("/api/x"));
    expect(a.headers.get("x-request-id")).toMatch(/^[0-9a-f]{32}$/);
    expect(a.headers.get("x-request-id")).not.toBe(b.headers.get("x-request-id"));
    expect(seen).toEqual([a.headers.get("x-request-id"), b.headers.get("x-request-id")]);
    expect(JSON.stringify(lines())).not.toContain("browser-chosen-id");
  });

  it("is not available outside a handler (a server-rendered page makes its own)", () => {
    expect(currentRequestId()).toBeUndefined();
    expect(newRequestId()).toMatch(/^[0-9a-f]{32}$/);
  });

  it.each(["x".repeat(5000), "line\nbreak", '{"event":"forged"}', "../../etc", "ünï"])("a hostile browser value (%#) never reaches a log or the response", async (hostile) => {
    const handler = instrument(async () => NextResponse.json({}));
    const response = await handler(request("/api/x", { "x-request-id": encodeURIComponent(hostile) }));
    expect(response.headers.get("x-request-id")).toMatch(/^[0-9a-f]{32}$/);
    expect(out.join("")).not.toContain(hostile.slice(0, 20));
  });
});

describe("the request line", () => {
  it("carries exactly the approved fields and the path WITHOUT the query string", async () => {
    const handler = instrument(async () => NextResponse.json({}, { status: 404 }));
    await handler(request(`/api/o/abc/customers?token=${SENTINEL}&x=1`, { cookie: `bp_session=${SENTINEL}`, authorization: `Bearer ${SENTINEL}`, "x-csrf-token": SENTINEL }, "POST"));
    const [line] = lines();
    expect(Object.keys(line).sort()).toEqual(["duration_ms", "event", "level", "method", "path", "request_id", "service", "status", "ts"]);
    expect(line).toMatchObject({ level: "info", service: "frontend", event: "request", method: "POST", path: "/api/o/abc/customers", status: 404 });
    expect(typeof line.duration_ms).toBe("number");
    expect(out.join("") + err.join("")).not.toContain(SENTINEL);
  });

  it("a 5xx is an error line", async () => {
    await instrument(async () => NextResponse.json({}, { status: 502 }))(request());
    expect(lines()[0]).toMatchObject({ level: "error", status: 502 });
    expect(err.length).toBe(1);
  });

  it("an unhandled error becomes a fixed 500 and is logged by CLASS, never by message", async () => {
    const handler = instrument(async (): Promise<NextResponse> => {
      throw new TypeError(`connect ECONNREFUSED backend.internal:8000 password=${SENTINEL}`);
    });
    const response = await handler(request());
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ detail: "Internal error" });
    const all = lines();
    expect(all.map((line) => line.event)).toEqual(["unhandled_error", "request"]);
    expect(all[0].error_type).toBe("TypeError");
    expect(out.join("") + err.join("")).not.toContain(SENTINEL);
    expect(out.join("") + err.join("")).not.toContain("backend.internal");
  });

  it("every record is one JSON line even when a value holds control characters", () => {
    logEvent("info", "probe", { value: 'a\nb\r"c' + "x".repeat(1000) });
    expect(out.join("").split("\n").filter(Boolean)).toHaveLength(1);
    expect((lines()[0].value as string).length).toBeLessThanOrEqual(300);
  });

  it("errorType never returns a message", () => {
    expect(errorType(new RangeError(SENTINEL))).toBe("RangeError");
    expect(errorType(SENTINEL)).toBe("string");
  });
});
