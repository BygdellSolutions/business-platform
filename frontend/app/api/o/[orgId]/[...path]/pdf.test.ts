// @vitest-environment node
import { createHash } from "node:crypto";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_PDF_BYTES, pdfPassThrough } from "@/lib/pdf-response";

import * as route from "./route";

/**
 * The invoice PDF through the BFF: the one binary resource. It is validated (a 200 with a real PDF whose
 * SHA-256 is the ETag the backend named), its headers are rebuilt from validated values, and anything else
 * is a 502 that never lets the bytes reach the browser. (The generic BFF rules are in route.test.ts.)
 */

const ORG = "00000000-0000-4000-8000-0000000000a1";
const OTHER_ORG = "00000000-0000-4000-8000-0000000000b2";
const ORIGIN = "http://localhost:3100";
const INVOICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const PATH = ["invoices", INVOICE, "pdf"];
// Binary-safe on purpose: NUL, 0xFF, 0x80 and CR/LF must survive untouched (they would not survive being read as text).
const BYTES = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x00, 0xff, 0xfe, 0x80, 0x0d, 0x0a, 0x25, 0x25, 0x45, 0x4f, 0x46]);

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

function pdfResponse(overrides: { bytes?: Uint8Array; status?: number; headers?: Record<string, string | null> } = {}) {
  const bytes = overrides.bytes ?? BYTES;
  const headers: Record<string, string> = {
    "content-type": "application/pdf",
    "content-disposition": 'attachment; filename="invoice-7.pdf"',
    "content-length": String(bytes.length),
    etag: `"${sha(bytes)}"`,
    "x-content-type-options": "nosniff",
    "cache-control": "private, no-store",
  };
  for (const [name, value] of Object.entries(overrides.headers ?? {})) {
    if (value === null) delete headers[name];
    else headers[name] = value;
  }
  return new Response(bytes as unknown as BodyInit, { status: overrides.status ?? 200, headers });
}

interface Options {
  path?: string[];
  query?: string;
  headers?: Record<string, string>;
  cookie?: string | null;
}

async function call(method: "GET" | "POST" | "DELETE", options: Options = {}) {
  const { path = PATH, query = "", headers = {}, cookie = "maria@dev.test" } = options;
  const url = `${ORIGIN}/api/o/${ORG}/${path.join("/")}${query}`;
  const request = new NextRequest(url, { method, headers: { ...(cookie === null ? {} : { cookie: `bp_dev_user=${cookie}` }), ...headers } });
  return route[method](request, { params: Promise.resolve({ orgId: ORG, path }) });
}

function sent() {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Headers }];
  return { url, headers: init.headers };
}

describe("a valid PDF", () => {
  it("passes the exact bytes through with headers rebuilt from validated values", async () => {
    fetchMock.mockResolvedValue(pdfResponse());

    const response = await call("GET");

    expect(response.status).toBe(200);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(BYTES);
    expect(response.headers.get("content-type")).toBe("application/pdf");
    expect(response.headers.get("content-disposition")).toBe('attachment; filename="invoice-7.pdf"');
    expect(response.headers.get("content-length")).toBe(String(BYTES.length));
    expect(response.headers.get("etag")).toBe(`"${sha(BYTES)}"`);
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });

  it("is asked for as a PDF with the BFF's own identity headers, whatever the client sent", async () => {
    fetchMock.mockResolvedValue(pdfResponse());

    await call("GET", { headers: { "x-dev-user-email": "evil@x.test", "x-organization-id": OTHER_ORG, accept: "text/html" } });

    expect(sent().url).toBe(`http://backend.test:8000/api/invoices/${INVOICE}/pdf`);
    expect(sent().headers.get("accept")).toContain("application/pdf");
    expect(sent().headers.get("x-dev-user-email")).toBe("maria@dev.test");
    expect(sent().headers.get("x-organization-id")).toBe(ORG);
  });

  it("leaks no other backend header", async () => {
    fetchMock.mockResolvedValue(pdfResponse({ headers: { "set-cookie": "session=abc", "x-secret": "1", server: "uvicorn", "access-control-allow-origin": "*" } }));

    const response = await call("GET");

    for (const name of ["set-cookie", "x-secret", "server", "access-control-allow-origin"]) expect(response.headers.get(name)).toBeNull();
  });

  it.each([
    ['attachment; filename="../../evil.pdf"', "invoice.pdf"],
    ['attachment; filename="invoice-7.exe"', "invoice.pdf"],
    ['attachment; filename="Umeå HK.pdf"', "invoice.pdf"],
    ['attachment; filename="invoice-7.pdf"; x=y', "invoice.pdf"],
    ["attachment", "invoice.pdf"],
    [`attachment; filename="invoice-${"x".repeat(61)}.pdf"`, "invoice.pdf"],
    ['attachment; filename="invoice.pdf"', "invoice.pdf"],
    ['attachment; filename="invoice-INV_2026.10-1.pdf"', "invoice-INV_2026.10-1.pdf"],
  ])("rebuilds the disposition from %j as the filename %j", async (given, filename) => {
    fetchMock.mockResolvedValue(pdfResponse({ headers: { "content-disposition": given } }));

    const response = await call("GET");

    expect(response.status).toBe(200);
    expect(response.headers.get("content-disposition")).toBe(`attachment; filename="${filename}"`);
  });
});

describe("a response that is not that PDF", () => {
  it.each([
    ["an inline disposition", { "content-disposition": 'inline; filename="invoice-7.pdf"' }],
    ["no disposition", { "content-disposition": null }],
    ["a text/html type", { "content-type": "text/html" }],
    ["an octet-stream type", { "content-type": "application/octet-stream" }],
    ["a type with parameters", { "content-type": "application/pdf; charset=utf-8" }],
    ["no type", { "content-type": null }],
    ["no etag", { etag: null }],
    ["a malformed etag", { etag: '"abc"' }],
    ["an etag that is not the content's hash", { etag: `"${"0".repeat(64)}"` }],
    ["a weak etag", { etag: `W/"${sha(BYTES)}"` }],
    ["no content length", { "content-length": null }],
    ["a content length that is too small to be a PDF", { "content-length": "3" }],
    ["a content length longer than the body", { "content-length": String(BYTES.length + 10) }],
    ["a content length shorter than the body", { "content-length": String(BYTES.length - 3) }],
    ["a content length beyond the limit", { "content-length": String(MAX_PDF_BYTES + 1) }],
  ])("is refused: a 200 with %s (502; no bytes reach the browser)", async (_name, headers) => {
    fetchMock.mockResolvedValue(pdfResponse({ headers }));

    const response = await call("GET");

    expect(response.status).toBe(502);
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(await response.json()).toEqual({ detail: "Unexpected response from the backend" });
  });

  it.each([
    ["HTML", new TextEncoder().encode("<html><script>alert(1)</script></html>")],
    ["an almost-PDF", new TextEncoder().encode("%PDX-1.4 ...........")],
    ["text with a leading space", new TextEncoder().encode(" %PDF-1.4 ..........")],
    ["too short", new TextEncoder().encode("%PDF")],
  ])("is refused: a body that is %s, even with all the right headers", async (_name, bytes) => {
    fetchMock.mockResolvedValue(pdfResponse({ bytes }));

    expect((await call("GET")).status).toBe(502);
  });

  it("refuses a declared length beyond the limit WITHOUT reading the body", async () => {
    let read = false;
    const body = new ReadableStream<Uint8Array>(
      {
        pull(controller) {
          read = true;
          controller.enqueue(BYTES);
          controller.close();
        },
      },
      { highWaterMark: 0 }, // no eager read: `pull` runs only when somebody asks for the body
    );
    fetchMock.mockResolvedValue(new Response(body, { status: 200, headers: { "content-type": "application/pdf", "content-disposition": 'attachment; filename="invoice-7.pdf"', "content-length": String(MAX_PDF_BYTES + 1), etag: `"${sha(BYTES)}"` } }));

    const response = await call("GET");

    expect(response.status).toBe(502);
    expect(read).toBe(false);
  });

  it.each([201, 202, 206, 404, 500])("pdfPassThrough itself refuses a %s even if everything else looks right", async (status) => {
    const response = await pdfPassThrough(pdfResponse({ status }));
    expect(response.status).toBe(502);
  });

  it("is refused: a 200 that is JSON where a PDF was expected", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));

    expect((await call("GET")).status).toBe(502);
  });

  it("is refused when a PDF arrives as an error response (or an error is not JSON)", async () => {
    fetchMock.mockResolvedValue(pdfResponse({ status: 409 }));
    expect((await call("GET")).status).toBe(502);

    fetchMock.mockResolvedValue(new Response("not json", { status: 404, headers: { "content-type": "text/plain" } }));
    expect((await call("GET")).status).toBe(502);
  });

  it("is refused on every other path: a PDF content type is never relayed as text", async () => {
    fetchMock.mockResolvedValue(pdfResponse());

    expect((await call("GET", { path: ["customers"] })).status).toBe(502);
    expect((await call("GET", { path: ["invoices", INVOICE] })).status).toBe(502);
  });

  it("answers 502 when the backend cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    expect((await call("GET")).status).toBe(502);
  });
});

describe("errors from the backend", () => {
  it.each([
    [404, { detail: "Not found" }],
    [409, { detail: { code: "invoice_not_issued", message: "Only an issued invoice has a PDF." } }],
    [422, { detail: { code: "unsupported_characters", message: "no", characters: [{ character: "U+0645", reason: "x" }], total: 1 } }],
  ])("a %s is relayed as JSON with its status and body", async (status, body) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

    const response = await call("GET");

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});

describe("what the PDF path accepts", () => {
  it.each([
    ["a query string", "GET" as const, { query: "?download=1" }],
    ["a POST", "POST" as const, { headers: { origin: ORIGIN } }],
    ["a DELETE", "DELETE" as const, { headers: { origin: ORIGIN } }],
  ])("refuses %s without calling the backend", async (_name, method, options) => {
    const response = await call(method, options);

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is only /invoices/{uuid}/pdf: any other shape is not treated as a PDF", async () => {
    for (const path of [["invoices", "pdf"], ["invoices", INVOICE, "pdf", "x"], ["invoices", "not-a-uuid", "pdf"], ["customers", INVOICE, "pdf"], ["invoices", INVOICE, "PDF.pdf"]]) {
      fetchMock.mockReset();
      fetchMock.mockResolvedValue(pdfResponse());

      const response = await call("GET", { path });

      expect(response.status).not.toBe(200);
      expect(response.headers.get("content-type")).not.toBe("application/pdf");
    }
  });

  it("needs a session and calls nothing without one", async () => {
    const response = await call("GET", { cookie: null });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
