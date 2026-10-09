import "server-only";

import { createHash } from "node:crypto";
import { NextResponse } from "next/server";

/**
 * The BFF's one binary pass-through: the frozen PDF of an invoice.
 *
 * Nothing the backend says about the file is relayed as it came. The response is checked to BE what
 * it should be (a 200 with a PDF body of a sane size whose SHA-256 is the ETag the backend named),
 * and the headers sent to the browser are REBUILT from validated values: the filename is accepted only
 * in the exact shape the backend makes (`invoice[-number].pdf`, ASCII letters, digits, dot, underscore,
 * hyphen) and is otherwise replaced by `invoice.pdf`. A response that is not that PDF is a 502; its
 * bytes never reach the browser.
 */

/** Matches the backend's renderer limit (a larger file is a bug, not an invoice). */
export const MAX_PDF_BYTES = 64 * 1024 * 1024;

const FILENAME = /^(?:invoice|credit-note)(?:-[A-Za-z0-9._-]{1,60})?\.pdf$/;
const DISPOSITION = /^attachment;\s*filename="([^"\\\r\n]*)"$/;
const ETAG = /^"([0-9a-f]{64})"$/;
const MAGIC = [0x25, 0x50, 0x44, 0x46, 0x2d]; // "%PDF-"
const NO_STORE = "private, no-store";

export function unexpected(): NextResponse {
  return NextResponse.json({ detail: "Unexpected response from the backend" }, { status: 502, headers: { "cache-control": "no-store" } });
}

/** The filename the backend offered if it has the expected shape; otherwise the neutral default. */
export function safeFilename(disposition: string | null): string {
  const found = DISPOSITION.exec(disposition ?? "");
  return found && FILENAME.test(found[1]) ? found[1] : "invoice.pdf";
}

export async function pdfPassThrough(upstream: Response): Promise<NextResponse> {
  if (upstream.status !== 200) return unexpected();
  if ((upstream.headers.get("content-type") ?? "").toLowerCase().trim() !== "application/pdf") return unexpected();
  if (!(upstream.headers.get("content-disposition") ?? "").toLowerCase().startsWith("attachment")) return unexpected();
  const etag = ETAG.exec(upstream.headers.get("etag") ?? "");
  if (etag === null) return unexpected();

  const declared = Number.parseInt(upstream.headers.get("content-length") ?? "", 10);
  if (!Number.isFinite(declared) || declared <= MAGIC.length || declared > MAX_PDF_BYTES) return unexpected();

  const bytes = new Uint8Array(await upstream.arrayBuffer());
  if (bytes.length !== declared || bytes.length > MAX_PDF_BYTES) return unexpected();
  if (!MAGIC.every((byte, index) => bytes[index] === byte)) return unexpected();
  if (createHash("sha256").update(bytes).digest("hex") !== etag[1]) return unexpected();

  return new NextResponse(bytes, {
    status: 200,
    headers: {
      "content-type": "application/pdf",
      "content-disposition": `attachment; filename="${safeFilename(upstream.headers.get("content-disposition"))}"`,
      "content-length": String(bytes.length),
      etag: etag[0],
      "x-content-type-options": "nosniff",
      "cache-control": NO_STORE,
    },
  });
}
