"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { apiFetch } from "@/lib/api/client";
import type { HorseDocument } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";
import { formatDay } from "@/lib/timestamps";

/** What the file picker offers. The backend decides what is accepted (and checks the bytes); this only saves a wasted upload. */
const ACCEPT = "application/pdf,image/jpeg,image/png,image/webp";
const TYPES = ACCEPT.split(",");
const MAX_BYTES = 10 * 1024 * 1024;

/** The file as base64 (the browser's only door to the backend carries JSON), without the `data:` prefix. */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Files kept with a horse: a vet report, an x-ray photo, a passport scan (PDF, JPEG, PNG or WebP, up to 10 MB). Pictures are shown as
 * small previews, a PDF as a file name; either opens in a new tab. Whoever may change records adds and deletes them (each step is in the
 * horse's history); everyone else can open them.
 */
export function HorseDocuments({ horseId, documents, canWrite, timeZone }: { horseId: string; documents: HorseDocument[]; canWrite: boolean; timeZone: string | null }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // A structured refusal ({code, message}) carries its own wording; `problemsFrom` only knows field errors.
  const messages = error?.kind === "validation" && error.code !== undefined ? [error.message] : problemsFrom(error, []).general;
  const fileUrl = (id: string) => `/api/o/${encodeURIComponent(orgId)}/horses/${horseId}/documents/${id}/file`;

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setFile(null);
    if (chosen === null) return setProblem(null);
    if (!TYPES.includes(chosen.type)) return setProblem("Choose a PDF, JPEG, PNG or WebP file.");
    if (chosen.size > MAX_BYTES) return setProblem("The file is larger than 10 MB.");
    setProblem(null);
    setFile(chosen);
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (file === null) return;
    const data_base64 = await readAsBase64(file);
    const saved = await run(() => apiFetch<HorseDocument>(orgId, `/horses/${horseId}/documents`, { method: "POST", body: { filename: file.name, content_type: file.type, data_base64 } }));
    if (saved === null) return;
    setFile(null);
    if (input.current) input.current.value = "";
    router.refresh();
  }

  async function remove(id: string) {
    const removed = await run(() => apiFetch<null>(orgId, `/horses/${horseId}/documents/${id}`, { method: "DELETE" }));
    if (removed !== null) router.refresh();
  }

  return (
    <section aria-label="Documents" data-testid="horse-documents" className="panel flex max-w-3xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Documents</h2>
      {canWrite && (
        <form onSubmit={upload} noValidate aria-label="Add document" className="flex flex-wrap items-center gap-3">
          <input ref={input} type="file" accept={ACCEPT} onChange={onChoose} className="sr-only" id="document-file" data-testid="document-input" disabled={pending} />
          <Button type="button" disabled={pending} onClick={() => input.current?.click()} data-testid="choose-document">
            Choose file
          </Button>
          <span className="text-sm text-secondary" data-testid="document-chosen">
            {file ? `${file.name} (${sizeText(file.size)})` : "PDF or picture, up to 10 MB"}
          </span>
          <Button type="submit" disabled={pending || file === null} data-testid="add-document">
            {pending ? "Uploading…" : "Add document"}
          </Button>
        </form>
      )}
      {problem && (
        <p role="alert" className="text-sm text-red-700" data-testid="document-problem">
          {problem}
        </p>
      )}
      <ErrorSummary messages={messages} />
      {documents.length === 0 ? (
        <p className="text-sm text-secondary" data-testid="no-documents">
          No documents yet.{canWrite ? " Add a PDF or a picture, for example a vet report or a photo of the passport." : ""}
        </p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2" data-testid="document-list">
          {documents.map((document) => (
            <li key={document.id} data-testid="horse-document" className="flex gap-3 rounded-lg border border-border p-3">
              <a href={fileUrl(document.id)} target="_blank" rel="noopener" aria-label={`Open ${document.filename}`} className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded bg-surface-hover">
                {document.content_type.startsWith("image/") ? (
                  // eslint-disable-next-line @next/next/no-img-element -- the file comes from the BFF, which is not an image the optimizer can fetch
                  <img src={fileUrl(document.id)} alt="" loading="lazy" className="size-full object-cover" data-testid="document-preview" />
                ) : (
                  <span className="text-xs font-semibold text-secondary">PDF</span>
                )}
              </a>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <a href={fileUrl(document.id)} target="_blank" rel="noopener" className="truncate font-medium underline" data-testid="document-name" title={document.filename}>
                  {document.filename}
                </a>
                <span className="text-xs text-secondary">
                  {sizeText(document.byte_size)} · {formatDay(document.created_at, timeZone)} · {document.created_by_name ?? "not recorded"}
                </span>
                {canWrite && (
                  <div>
                    <ConfirmButton
                      label="Delete"
                      question="Delete this document for good? The history keeps its name, not its content."
                      confirmLabel="Yes, delete"
                      disabled={pending}
                      onConfirm={() => void remove(document.id)}
                      testId="delete-document"
                    />
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
