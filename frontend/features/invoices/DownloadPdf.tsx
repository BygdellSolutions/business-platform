"use client";

import { useEffect, useRef, useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { apiDownloadPdf, type DownloadedFile } from "@/lib/api/client";
import type { ApiError } from "@/lib/api/errors";
import type { Invoice } from "@/lib/api/types";

/**
 * "Download PDF" for an ISSUED invoice (a draft has no PDF and no control for one).
 *
 * The PDF is made by the backend, once, from the stored invoice; this only asks for it and hands the
 * bytes to the browser as a file download. Nothing is generated or calculated here. Any member who can
 * read the invoice can use it, whatever their role. The first request for an invoice may take a moment
 * (that is when the backend makes the file); the button says so and cannot be pressed twice. A failure is
 * explained and nothing is assumed: pressing the button again is always safe (the backend serves the file
 * it already stored, or tries again if it stored none).
 */

export interface PdfFailureText {
  text: string;
  details: string[];
}

const MAX_SHOWN = 8;

export function pdfFailureText(error: ApiError): PdfFailureText {
  switch (error.kind) {
    case "not_found":
      return { text: "This invoice no longer exists, or you do not have access to it.", details: [] };
    case "conflict":
      if (error.code === "invoice_not_issued") return { text: "Only an issued invoice has a PDF. Reload the page to see the invoice's current state.", details: [] };
      return { text: error.message, details: [] };
    case "validation":
      if (error.code === "unsupported_characters") {
        const shown = (error.characters ?? []).slice(0, MAX_SHOWN).map((c) => `${c.character}: ${c.reason}`);
        const more = (error.totalCharacters ?? shown.length) - shown.length;
        if (more > 0) shown.push(`and ${more} more`);
        return { text: error.message, details: shown };
      }
      return { text: error.message, details: [] };
    case "unauthorized":
      return { text: "You are not signed in.", details: [] };
    case "forbidden":
      return { text: error.message, details: [] };
    case "network":
    case "server":
      return { text: "The PDF could not be prepared right now. Nothing was changed; try again.", details: [] };
    default:
      return { text: error.message, details: [] };
  }
}

/** Hand the file to the browser as a download. */
function saveFile(file: DownloadedFile) {
  const url = URL.createObjectURL(file.blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = file.filename;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function DownloadPdf({ invoice }: { invoice: Pick<Invoice, "id" | "status"> }) {
  const orgId = useOrgId();
  const [preparing, setPreparing] = useState(false);
  const [failure, setFailure] = useState<PdfFailureText | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const running = useRef(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  if (invoice.status !== "issued") return null;

  async function download() {
    if (running.current) return;
    running.current = true;
    const controller = new AbortController();
    abort.current = controller;
    setPreparing(true);
    setFailure(null);
    setDone(null);
    try {
      const result = await apiDownloadPdf(orgId, `/invoices/${invoice.id}/pdf`, controller.signal);
      if (controller.signal.aborted) return;
      if (result.ok) {
        saveFile(result.data);
        setDone(result.data.filename);
      } else {
        setFailure(pdfFailureText(result.error));
      }
    } catch {
      // Only a cancelled request throws (the screen was left): there is nobody to tell.
    } finally {
      running.current = false;
      if (!controller.signal.aborted) setPreparing(false);
    }
  }

  return (
    <section aria-label="PDF" data-testid="pdf" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" disabled={preparing} aria-busy={preparing} onClick={() => void download()} data-testid="download-pdf">
          {preparing ? "Preparing PDF…" : failure ? "Try again" : "Download PDF"}
        </Button>
        {done && (
          <span role="status" data-testid="pdf-done" className="text-sm text-zinc-600 dark:text-zinc-400">
            Downloaded {done}.
          </span>
        )}
      </div>
      {failure && (
        <Notice tone="error" testId="pdf-error">
          <p>{failure.text}</p>
          {failure.details.length > 0 && (
            <ul data-testid="pdf-error-details" className="mt-1 list-disc pl-5">
              {failure.details.map((detail) => (
                <li key={detail}>{detail}</li>
              ))}
            </ul>
          )}
        </Notice>
      )}
    </section>
  );
}
