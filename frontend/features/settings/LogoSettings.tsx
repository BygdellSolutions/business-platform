"use client";

import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { Organization, OrganizationLogo } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";

/** The backend's own limit (700 kB): checking here only saves a round trip, FastAPI still decides. */
const MAX_BYTES = 700_000;
const ACCEPTED = ["image/png", "image/jpeg"];

/** The file as base64 (the browser's only door to the backend carries JSON), without the `data:` prefix. */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/** The logo printed on the organization's invoices, credit notes and receipts. Only the owner may change it. */
export function LogoSettings({ logo, canEdit }: { logo: OrganizationLogo | null; canEdit: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<"saved" | "removed" | null>(null);
  // A structured refusal ({code, message}, here "invalid_logo") carries its own wording; `problemsFrom` only knows field errors.
  const messages = error?.kind === "validation" && error.code !== undefined ? [error.message] : problemsFrom(error, []).general;

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setNotice(null);
    setFile(null);
    if (chosen === null) return setProblem(null);
    if (!ACCEPTED.includes(chosen.type)) return setProblem("The logo must be a PNG or JPEG image.");
    if (chosen.size > MAX_BYTES) return setProblem("The file is larger than 700 KB.");
    setProblem(null);
    setFile(chosen);
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (file === null) return;
    const data_base64 = await readAsBase64(file);
    const saved = await run(() => apiFetch<Organization>(orgId, "/organization/logo", { method: "POST", body: { data_base64 } }));
    if (saved === null) return;
    setFile(null);
    if (input.current) input.current.value = "";
    setNotice("saved");
    router.refresh(); // the preview is read again from the backend
  }

  async function remove() {
    setNotice(null);
    const done = await run(() => apiFetch<unknown>(orgId, "/organization/logo", { method: "DELETE" }));
    if (done === null) return;
    setNotice("removed");
    router.refresh();
  }

  return (
    <section className="panel flex max-w-xl flex-col gap-4" aria-label="Logo">
      <div>
        <h2 className="text-base font-semibold">Logo</h2>
        <p className="text-sm text-muted">
          Printed at the top of new invoices, credit notes and receipts. An invoice keeps the logo it was issued with, so changing or removing it never alters an issued invoice.
        </p>
      </div>
      {logo !== null ? (
        // eslint-disable-next-line @next/next/no-img-element -- a data: URL of the stored PNG; next/image has nothing to optimize
        <img
          src={`data:image/png;base64,${logo.data_base64}`}
          alt="The organization's logo"
          width={logo.width}
          height={logo.height}
          data-testid="logo-preview"
          className="max-h-24 w-auto max-w-xs self-start rounded border border-border bg-white p-2 object-contain"
        />
      ) : (
        <p className="text-sm" data-testid="no-logo">
          No logo yet.
        </p>
      )}
      {canEdit ? (
        <form onSubmit={(event) => void upload(event)} className="flex flex-col gap-3" aria-label="Upload logo">
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium">{logo !== null ? "Replace logo" : "Upload logo"}</span>
            {/* The browser's own file control cannot be styled or reworded, so it is hidden and this button opens it. */}
            <input ref={input} type="file" accept="image/png,image/jpeg" onChange={onChoose} data-testid="logo-file" aria-label="Logo file" tabIndex={-1} className="sr-only" />
            <div className="flex items-center gap-3">
              <Button type="button" onClick={() => input.current?.click()} disabled={pending} data-testid="logo-choose">
                Choose file
              </Button>
              {file !== null && <span className="min-w-0 truncate text-sm" data-testid="logo-file-name">{file.name}</span>}
            </div>
            <span className="text-xs text-muted">PNG or JPEG, up to 700 KB. Transparent PNGs work. A wide or tall logo is scaled to fit.</span>
          </div>
          {problem !== null && <ErrorSummary messages={[problem]} />}
          <ErrorSummary messages={messages} />
          {notice === "saved" && <Notice testId="logo-saved">Logo saved.</Notice>}
          {notice === "removed" && <Notice testId="logo-removed">Logo removed.</Notice>}
          <div className="flex gap-2">
            <Button type="submit" disabled={pending || file === null} data-testid="logo-upload">
              {pending ? "Saving…" : logo !== null ? "Replace logo" : "Upload logo"}
            </Button>
            {logo !== null && (
              <Button type="button" variant="danger" disabled={pending} onClick={() => void remove()} data-testid="logo-remove">
                Remove logo
              </Button>
            )}
          </div>
        </form>
      ) : (
        <p className="text-sm text-muted" data-testid="logo-read-only">
          Only the owner can change the logo.
        </p>
      )}
    </section>
  );
}
