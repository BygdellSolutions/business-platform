"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { apiFetch } from "@/lib/api/client";
import type { JournalEntry } from "@/lib/api/types";
import { useMutation } from "@/lib/forms";

/** Starts a visit: the draft is created now (empty, dated today, by you) and opened for writing. It stays in the list until finalized. */
export function NewJournalEntry({ horseId, children }: { horseId: string; children?: ReactNode }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();

  async function start() {
    const entry = await run(() => apiFetch<JournalEntry>(orgId, `/horses/${horseId}/journal`, { method: "POST", body: {} }));
    if (entry !== null) router.push(`/o/${orgId}/horses/${horseId}/journal/${entry.id}`);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="primary" disabled={pending} onClick={() => void start()} data-testid="new-journal-entry">
          {pending ? "Starting…" : "New entry"}
        </Button>
        {children}
      </div>
      {error && <ErrorSummary messages={[error.message]} />}
    </div>
  );
}
