"use client";

import { useRouter } from "next/navigation";

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
  onChanged,
}: {
  path: string;
  active: boolean;
  noun: string;
  onChanged: (record: T) => void;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();

  async function toggle() {
    const record = await run(() => apiFetch<T>(orgId, path, { method: "PATCH", body: { active: !active } }));
    if (record === null) return;
    onChanged(record);
    router.refresh(); // simple V1 strategy: re-render the server components with the new state
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <StatusBadge active={active} />
        <Button type="button" onClick={toggle} disabled={pending} data-testid="toggle-active">
          {active ? `Deactivate ${noun}` : `Reactivate ${noun}`}
        </Button>
      </div>
      <ErrorSummary messages={problemsFrom(error, []).general} />
    </div>
  );
}
