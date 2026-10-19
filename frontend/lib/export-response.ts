import "server-only";

import { NextResponse } from "next/server";

/**
 * The BFF's second binary pass-through: the complete copy of an organization's data (a ZIP), POST only, owner only.
 *
 * It is STREAMED (a copy can be large: nothing is buffered here), so what can be checked is what is known before the first
 * byte: a 200 whose type is `application/zip` and which is offered as an attachment. The headers sent to the browser are rebuilt:
 * the file name is accepted only in the exact shape the backend makes (`export-<ascii name>-<date>.zip`) and is otherwise
 * replaced by `export.zip`. Anything else is a 502 and no byte of it reaches the browser.
 */

const FILENAME = /^export-[a-z0-9-]{1,48}-\d{4}-\d{2}-\d{2}\.zip$/;
const DISPOSITION = /^attachment;\s*filename="([^"\\\r\n]*)"$/;

export const EXPORT_PATH = "/api/organization/export";

/** The filename the backend offered if it has the expected shape; otherwise the neutral default. */
export function safeExportFilename(disposition: string | null): string {
  const found = DISPOSITION.exec(disposition ?? "");
  return found && FILENAME.test(found[1]) ? found[1] : "export.zip";
}

export function exportPassThrough(upstream: Response): NextResponse {
  const type = (upstream.headers.get("content-type") ?? "").toLowerCase().trim();
  if (upstream.status !== 200 || type !== "application/zip" || upstream.body === null) return unexpectedExport();
  if (!(upstream.headers.get("content-disposition") ?? "").toLowerCase().startsWith("attachment")) return unexpectedExport();
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "content-type": "application/zip",
      "content-disposition": `attachment; filename="${safeExportFilename(upstream.headers.get("content-disposition"))}"`,
      "x-content-type-options": "nosniff",
      "cache-control": "private, no-store",
    },
  });
}

export function unexpectedExport(): NextResponse {
  return NextResponse.json({ detail: "Unexpected response from the backend" }, { status: 502, headers: { "cache-control": "no-store" } });
}
