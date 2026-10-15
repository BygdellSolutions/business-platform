// @vitest-environment node
import { describe, expect, it } from "vitest";

import { exportPassThrough, safeExportFilename } from "@/lib/export-response";

const zipResponse = (body: BodyInit | null = "PK", headers: Record<string, string> = {}, status = 200) =>
  new Response(body, {
    status,
    headers: { "content-type": "application/zip", "content-disposition": 'attachment; filename="export-ume-hast-rehab-2026-10-15.zip"', ...headers },
  });

describe("safeExportFilename", () => {
  it("keeps the exact shape the backend makes", () => {
    expect(safeExportFilename('attachment; filename="export-ume-hast-rehab-2026-10-15.zip"')).toBe("export-ume-hast-rehab-2026-10-15.zip");
    expect(safeExportFilename('attachment; filename="export-organization-2026-01-02.zip"')).toBe("export-organization-2026-01-02.zip");
  });

  it.each([
    ['attachment; filename="../../etc/passwd"'],
    ['attachment; filename="export-x-2026-10-15.zip\\r\\nSet-Cookie: a=b"'],
    ['attachment; filename="export-UPPER-2026-10-15.zip"'],
    ['attachment; filename="invoice.pdf"'],
    ['inline; filename="export-x-2026-10-15.zip"'],
    [null],
  ])("replaces %s by a neutral name", (value) => {
    expect(safeExportFilename(value)).toBe("export.zip");
  });
});

describe("exportPassThrough", () => {
  it("passes the ZIP on with rebuilt headers", async () => {
    const response = exportPassThrough(zipResponse("PKdata", { "set-cookie": "x=y", "x-secret": "no" }));

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/zip");
    expect(response.headers.get("content-disposition")).toBe('attachment; filename="export-ume-hast-rehab-2026-10-15.zip"');
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("x-secret")).toBeNull();
    expect(await response.text()).toBe("PKdata");
  });

  it("STREAMS: the first bytes reach the browser while the rest has not been produced yet", async () => {
    let finish: () => void = () => undefined;
    const upstream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("first"));
        finish = () => controller.close();
      },
    });
    const response = exportPassThrough(zipResponse(upstream));

    const reader = response.body!.getReader();
    const first = await reader.read(); // would hang forever if the BFF buffered the whole body
    expect(new TextDecoder().decode(first.value)).toBe("first");
    expect(first.done).toBe(false);
    finish();
    expect((await reader.read()).done).toBe(true);
  });

  it.each([
    ["a status that is not 200", zipResponse("PK", {}, 202)],
    ["a different type", zipResponse("PK", { "content-type": "application/json" })],
    ["a PDF", zipResponse("%PDF-", { "content-type": "application/pdf" })],
    ["no attachment disposition", zipResponse("PK", { "content-disposition": "inline" })],
    ["no body", zipResponse(null)],
  ])("is a 502 for %s and none of its bytes pass", async (_name, upstream) => {
    const response = exportPassThrough(upstream);

    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("PK");
  });
});
