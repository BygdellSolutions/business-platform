// @vitest-environment node
import { createHash } from "node:crypto";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_DOCUMENT_UPLOAD_BODY_BYTES } from "@/lib/backend";
import { documentName } from "@/lib/document-response";

import * as route from "./route";

/**
 * A horse's documents through the BFF: the upload may be bigger than other requests (a 10 MB file as base64), and the file itself
 * is the third binary resource: validated (type, signature, size, SHA-256 = ETag) with its headers rebuilt, anything else a 502.
 */

const ORG = "00000000-0000-4000-8000-0000000000a1";
const ORIGIN = "http://localhost:3100";
const HORSE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const DOC = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const FILE = ["horses", HORSE, "documents", DOC, "file"];
const LIST = ["horses", HORSE, "documents"];
const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x00, 0xff, 0xfe, 0x80, 0x0d, 0x0a, 0x25, 0x25, 0x45, 0x4f, 0x46]);
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x01, 0x02, 0x03]);

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "dev");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");

function fileResponse(overrides: { bytes?: Uint8Array; type?: string; status?: number; headers?: Record<string, string | null> } = {}) {
  const bytes = overrides.bytes ?? PDF;
  const headers: Record<string, string> = {
    "content-type": overrides.type ?? "application/pdf",
    "content-disposition": "inline; filename=\"Rontgen.pdf\"; filename*=UTF-8''R%C3%B6ntgen.pdf",
    "content-length": String(bytes.length),
    etag: `"${sha(bytes)}"`,
  };
  for (const [name, value] of Object.entries(overrides.headers ?? {})) {
    if (value === null) delete headers[name];
    else headers[name] = value;
  }
  return new Response(bytes as unknown as BodyInit, { status: overrides.status ?? 200, headers });
}

function get(path: string[], query = "") {
  const request = new NextRequest(`${ORIGIN}/api/o/${ORG}/${path.join("/")}${query}`, { method: "GET", headers: { cookie: "bp_dev_user=maria@dev.test" } });
  return route.GET(request, { params: Promise.resolve({ orgId: ORG, path }) });
}

function post(path: string[], body: string) {
  const request = new NextRequest(`${ORIGIN}/api/o/${ORG}/${path.join("/")}`, {
    method: "POST",
    body,
    headers: { cookie: "bp_dev_user=maria@dev.test", "content-type": "application/json", origin: ORIGIN, "content-length": String(Buffer.byteLength(body)) },
  });
  return route.POST(request, { params: Promise.resolve({ orgId: ORG, path }) });
}

describe("the file of a document", () => {
  it("passes the exact bytes through with rebuilt headers", async () => {
    fetchMock.mockResolvedValue(fileResponse());

    const response = await get(FILE);

    expect(response.status).toBe(200);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(PDF);
    expect(response.headers.get("content-type")).toBe("application/pdf");
    expect(response.headers.get("content-disposition")).toBe("inline; filename=\"Rontgen.pdf\"; filename*=UTF-8''R%C3%B6ntgen.pdf");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("content-security-policy")).toBeNull(); // a PDF viewer needs more than a sandbox allows
  });

  it("sandboxes a picture", async () => {
    fetchMock.mockResolvedValue(fileResponse({ bytes: PNG, type: "image/png" }));
    const response = await get(FILE);
    expect(response.headers.get("content-type")).toBe("image/png");
    expect(response.headers.get("content-security-policy")).toBe("default-src 'none'; sandbox");
  });

  it.each([
    ["an HTML page", { type: "text/html" }],
    ["an SVG picture (it can carry script)", { type: "image/svg+xml" }],
    ["a type that does not match the bytes", { type: "image/png" }],
    ["a missing ETag", { headers: { etag: null } }],
    ["a wrong ETag", { headers: { etag: `"${"0".repeat(64)}"` } }],
    ["a length that does not match", { headers: { "content-length": "999" } }],
    ["a file over 10 MB", { headers: { "content-length": String(10 * 1024 * 1024 + 1) } }],
    ["an error status passed as a 200 body type", { status: 500 }],
  ])("answers 502 for %s and sends none of its bytes", async (_name, overrides) => {
    fetchMock.mockResolvedValue(fileResponse(overrides as Parameters<typeof fileResponse>[0]));
    const response = await get(FILE);
    expect([502, 503]).toContain(response.status);
    expect(await response.text()).not.toContain("%PDF");
  });

  it("only answers GET without a query", async () => {
    expect((await get(FILE, "?x=1")).status).toBe(404);
    const request = new NextRequest(`${ORIGIN}/api/o/${ORG}/${FILE.join("/")}`, { method: "DELETE", headers: { cookie: "bp_dev_user=maria@dev.test", origin: ORIGIN } });
    expect((await route.DELETE(request, { params: Promise.resolve({ orgId: ORG, path: FILE }) })).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not take the name from anything but the RFC 5987 part, and drops control characters and quotes", () => {
    expect(documentName("inline; filename=\"x\"; filename*=UTF-8''a%0D%0Ab%22c%2Fd.pdf")).toBe("abcd.pdf");
    expect(documentName("inline; filename=\"x\"")).toBe("document");
    expect(documentName("inline; filename*=UTF-8''%E0%A4%A")).toBe("document");
    expect(documentName(null)).toBe("document");
  });
});

describe("the upload", () => {
  it("accepts a body well over the usual 1 MB, and passes it on", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: DOC }), { status: 201, headers: { "content-type": "application/json" } }));
    const body = JSON.stringify({ filename: "a.pdf", content_type: "application/pdf", data_base64: "A".repeat(5_000_000) });

    const response = await post(LIST, body);

    expect(response.status).toBe(201);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses a body over the document limit before anything is forwarded", async () => {
    const body = JSON.stringify({ filename: "a.pdf", content_type: "application/pdf", data_base64: "A".repeat(MAX_DOCUMENT_UPLOAD_BODY_BYTES + 10) });
    const response = await post(LIST, body);
    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps the usual 1 MB limit everywhere else", async () => {
    const body = JSON.stringify({ title: "x".repeat(1_100_000) });
    const response = await post(["horses", HORSE, "notes"], body);
    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
