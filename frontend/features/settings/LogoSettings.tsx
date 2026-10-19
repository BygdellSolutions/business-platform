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
import { prepareLogo } from "@/lib/logo-picture";

/** What the file picker offers: any ordinary picture. A picture that is not a PNG or JPEG, or is over 700 KB, is converted here (see lib/logo-picture.ts). */
const ACCEPT = "image/png,image/jpeg,image/webp,image/gif";

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
  // The file as the person chose it (for its name) and the file that will be sent (the same, or a smaller copy of it).
  const [chosen, setChosen] = useState<File | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [resized, setResized] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<"saved" | "removed" | null>(null);
  // A structured refusal ({code, message}, here "invalid_logo") carries its own wording; `problemsFrom` only knows field errors.
  const messages = error?.kind === "validation" && error.code !== undefined ? [error.message] : problemsFrom(error, []).general;

  async function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0] ?? null;
    setNotice(null);
    setFile(null);
    setChosen(null);
    setResized(null);
    if (picked === null) return setProblem(null);
    setChosen(picked);
    setPreparing(true);
    const prepared = await prepareLogo(picked); // too big or not a PNG/JPEG: made into one that fits; otherwise untouched
    setPreparing(false);
    if ("problem" in prepared) {
      setChosen(null);
      return setProblem(prepared.problem);
    }
    setProblem(null);
    setFile(prepared.file);
    setResized(prepared.note);
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (file === null) return;
    const data_base64 = await readAsBase64(file);
    const saved = await run(() => apiFetch<Organization>(orgId, "/organization/logo", { method: "POST", body: { data_base64 } }));
    if (saved === null) return;
    setFile(null);
    setChosen(null);
    setResized(null);
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
          {/* The browser's own file control cannot be styled or reworded, so it is hidden and this button opens it. */}
          <input ref={input} type="file" accept={ACCEPT} onChange={(event) => void onChoose(event)} data-testid="logo-file" aria-label="Logo file" tabIndex={-1} className="sr-only" />
          <div className="flex items-center gap-3">
            <Button type="button" onClick={() => input.current?.click()} disabled={pending || preparing} data-testid="logo-choose">
              Choose file
            </Button>
            {chosen !== null && (
              <span className="min-w-0 truncate text-sm" data-testid="logo-file-name">
                {chosen.name}
              </span>
            )}
          </div>
          <span className="text-xs text-muted">A PNG or JPEG. A big picture is made smaller for you. Transparent PNGs work, and a wide or tall logo is scaled to fit.</span>
          {preparing && (
            <p className="text-sm text-secondary" role="status" data-testid="logo-preparing">
              Getting the picture ready…
            </p>
          )}
          {resized !== null && <Notice testId="logo-resized">{resized}</Notice>}
          {problem !== null && (
            <div data-testid="logo-problem">
              <ErrorSummary messages={[problem]} />
            </div>
          )}
          <ErrorSummary messages={messages} />
          {notice === "saved" && <Notice testId="logo-saved">Logo saved.</Notice>}
          {notice === "removed" && <Notice testId="logo-removed">Logo removed.</Notice>}
          <div className="flex gap-2">
            <Button type="submit" disabled={pending || preparing || file === null} data-testid="logo-upload">
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
