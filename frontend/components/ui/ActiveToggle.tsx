"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { apiFetch } from "@/lib/api/client";
import { problemsFrom, useMutation } from "@/lib/forms";

/**
 * Deactivate / reactivate in one request (`PATCH {active}`); records are deactivated, not
 * deleted, because other records may refer to them. It changes nothing else, so unsaved edits
 * in a form next to it are untouched. `path` is relative to the organization, e.g. "/customers/{id}".
 */
export function ActiveToggle<T extends { active: boolean }>({
  path,
  active,
  noun,
  version,
  onChanged,
  children,
}: {
  path: string;
  active: boolean;
  noun: string;
  /** The record's version, for records that guard changes with If-Match (items). */
  version?: number;
  onChanged: (record: T) => void;
  /** More buttons beside the toggle (for example a catalog item's Stock). */
  children?: ReactNode;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();

  async function toggle() {
    const record = await run(() => apiFetch<T>(orgId, path, { method: "PATCH", body: { active: !active }, ifMatch: version }));
    if (record === null) return;
    onChanged(record);
    router.refresh(); // simple V1 strategy: re-render the server components with the new state
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge active={active} />
        <Button type="button" onClick={toggle} disabled={pending} data-testid="toggle-active">
          {active ? `Deactivate ${noun}` : `Reactivate ${noun}`}
        </Button>
        {children}
      </div>
      <ErrorSummary messages={problemsFrom(error, []).general} />
    </div>
  );
}
