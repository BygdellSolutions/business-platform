import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiDownloadPdf, downloadFilename, setUnauthorizedHandler } from "@/lib/api/client";
import { normalizeError } from "@/lib/api/errors";

const ORG = "00000000-0000-4000-8000-0000000000a1";
const PATH = "/invoices/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/pdf";
const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x00, 0xff]);

let fetchMock: ReturnType<typeof vi.fn>;
let unauthorized: ReturnType<typeof vi.fn<() => void>>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  unauthorized = vi.fn<() => void>();
  setUnauthorizedHandler(unauthorized);
});
afterEach(() => vi.unstubAllGlobals());

const pdf = (headers: Record<string, string> = {}) =>
  new Response(PDF, { status: 200, headers: { "content-type": "application/pdf", "content-disposition": 'attachment; filename="invoice-7.pdf"', ...headers } });
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("apiDownloadPdf", () => {
  it("fetches through the same-origin BFF with no identity headers, and returns the bytes and the filename", async () => {
    fetchMock.mockResolvedValue(pdf());

    const result = await apiDownloadPdf(ORG, PATH);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/o/${ORG}${PATH}`);
    expect(init.method).toBe("GET");
    expect(Object.keys(init.headers).map((name) => name.toLowerCase())).toEqual(["accept"]);
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.data.filename).toBe("invoice-7.pdf");
    expect(result.data.blob.type).toBe("application/pdf");
    expect(new Uint8Array(await result.data.blob.arrayBuffer())).toEqual(PDF);
  });

  it("treats a 200 that is not a PDF as a failure (nothing is downloaded)", async () => {
    fetchMock.mockResolvedValue(new Response("<html>login</html>", { status: 200, headers: { "content-type": "text/html" } }));

    const result = await apiDownloadPdf(ORG, PATH);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.kind).toBe("server");
  });

  it.each([
    ['attachment; filename="../../x.pdf"', "invoice.pdf"],
    ['attachment; filename="evil.exe"', "invoice.pdf"],
    ["inline", "invoice.pdf"],
    ['attachment; filename="invoice-INV_2026.10-1.pdf"', "invoice-INV_2026.10-1.pdf"],
  ])("takes only a well-formed filename (%j -> %j)", async (given, filename) => {
    fetchMock.mockResolvedValue(pdf({ "content-disposition": given }));

    const result = await apiDownloadPdf(ORG, PATH);

    expect(result.ok && result.data.filename).toBe(filename);
  });

  it("downloadFilename has a neutral default", () => {
    expect(downloadFilename(null)).toBe("invoice.pdf");
  });

  it.each([
    [404, { detail: "Not found" }, "not_found"],
    [409, { detail: { code: "invoice_not_issued", message: "Only an issued invoice has a PDF." } }, "conflict"],
    [422, { detail: { code: "unsupported_characters", message: "cannot", characters: [], total: 0 } }, "validation"],
    [500, { detail: "boom" }, "server"],
  ])("a %s becomes a normalized error", async (status, body, kind) => {
    fetchMock.mockResolvedValue(json(status, body));

    const result = await apiDownloadPdf(ORG, PATH);

    expect(!result.ok && result.error.kind).toBe(kind);
  });

  it("calls the unauthorized handler on a 401", async () => {
    fetchMock.mockResolvedValue(json(401, { detail: "Not authenticated" }));

    await apiDownloadPdf(ORG, PATH);

    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it("returns a network error instead of throwing", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const result = await apiDownloadPdf(ORG, PATH);

    expect(!result.ok && result.error.kind).toBe("network");
  });

  it("rethrows a cancelled request so the screen can ignore it", async () => {
    const controller = new AbortController();
    controller.abort();
    fetchMock.mockRejectedValue(new DOMException("aborted", "AbortError"));

    await expect(apiDownloadPdf(ORG, PATH, controller.signal)).rejects.toThrow();
  });

  it("copes with an error body that is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("Bad gateway", { status: 502, headers: { "content-type": "text/plain" } }));

    const result = await apiDownloadPdf(ORG, PATH);

    expect(!result.ok && result.error.kind).toBe("server");
  });
});

describe("a structured 422 (the PDF could not be made)", () => {
  it("keeps the code, the message and the characters", () => {
    const error = normalizeError(422, {
      detail: { code: "unsupported_characters", message: "Cannot draw these.", characters: [{ character: "U+0645", reason: "complex" }], total: 3 },
    });

    expect(error).toMatchObject({ kind: "validation", status: 422, code: "unsupported_characters", message: "Cannot draw these.", totalCharacters: 3, characters: [{ character: "U+0645", reason: "complex" }], fieldErrors: {}, formErrors: [] });
  });

  it("leaves the field-error 422 exactly as it was", () => {
    const error = normalizeError(422, { detail: [{ loc: ["body", "name"], msg: "Value error, too long", type: "x" }] });

    expect(error).toMatchObject({ kind: "validation", fieldErrors: { name: ["too long"] } });
    expect("code" in error && error.code).toBeFalsy();
  });
});
