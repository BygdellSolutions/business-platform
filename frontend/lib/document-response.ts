import "server-only";

import { createHash } from "node:crypto";
import { NextResponse } from "next/server";

/**
 * The BFF's third binary pass-through: a file kept with a horse (a PDF or a picture), GET only.
 *
 * Nothing the backend says about the file is relayed as it came. The response must BE what it should be: a 200 whose type is one of
 * the four the backend accepts, of a sane size, whose SHA-256 is the ETag the backend named and whose first bytes match the type.
 * The headers sent to the browser are REBUILT from validated values: the type from the list, the name through RFC 5987 encoding of
 * what the backend sent (control characters removed), `nosniff`, and for everything but a PDF a CSP that lets a picture be a
 * picture and nothing else. Anything else is a 502 and none of its bytes reach the browser.
 */

export const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;

const SIGNATURES: Record<string, number[]> = {
  "application/pdf": [0x25, 0x50, 0x44, 0x46, 0x2d],
  "image/jpeg": [0xff, 0xd8, 0xff],
  "image/png": [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
  "image/webp": [0x52, 0x49, 0x46, 0x46],
};
const ETAG = /^"([0-9a-f]{64})"$/;
const FILENAME_STAR = /filename\*=UTF-8''([A-Za-z0-9%._~!$&'()*+,;=:@-]{1,700})(?:;|$)/;
const NO_STORE = "private, no-store";

export function unexpectedDocument(): NextResponse {
  return NextResponse.json({ detail: "Unexpected response from the backend" }, { status: 502, headers: { "cache-control": "no-store" } });
}

/** The name the backend offered, decoded and cleaned of control characters, or a neutral default. */
export function documentName(disposition: string | null): string {
  const found = FILENAME_STAR.exec(disposition ?? "");
  if (found === null) return "document";
  try {
    return decodeURIComponent(found[1]).replace(/[\u0000-\u001f\u007f"\\/]/g, "").slice(0, 200) || "document";
  } catch {
    return "document";
  }
}

export async function documentPassThrough(upstream: Response): Promise<NextResponse> {
  if (upstream.status !== 200) return unexpectedDocument();
  const type = (upstream.headers.get("content-type") ?? "").toLowerCase().trim();
  const signature = SIGNATURES[type];
  if (signature === undefined) return unexpectedDocument();
  const etag = ETAG.exec(upstream.headers.get("etag") ?? "");
  if (etag === null) return unexpectedDocument();

  const declared = Number.parseInt(upstream.headers.get("content-length") ?? "", 10);
  if (!Number.isFinite(declared) || declared <= signature.length || declared > MAX_DOCUMENT_BYTES) return unexpectedDocument();
  const bytes = new Uint8Array(await upstream.arrayBuffer());
  if (bytes.length !== declared) return unexpectedDocument();
  if (!signature.every((byte, index) => bytes[index] === byte)) return unexpectedDocument();
  if (createHash("sha256").update(bytes).digest("hex") !== etag[1]) return unexpectedDocument();

  const name = documentName(upstream.headers.get("content-disposition"));
  const ascii = name.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9._ -]+/g, "_") || "document";
  return new NextResponse(bytes, {
    status: 200,
    headers: {
      "content-type": type,
      "content-disposition": `inline; filename="${ascii}"; filename*=UTF-8''${encodeURIComponent(name)}`,
      "content-length": String(bytes.length),
      etag: etag[0],
      "x-content-type-options": "nosniff",
      "cache-control": NO_STORE,
      ...(type === "application/pdf" ? {} : { "content-security-policy": "default-src 'none'; sandbox" }),
    },
  });
}
