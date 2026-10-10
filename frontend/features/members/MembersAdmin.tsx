"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { useOrgId } from "@/components/shell/org-context";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { Notice } from "@/components/ui/Notice";
import { failureMessage } from "@/features/members/messages";
import { apiFetch } from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/errors";
import type { Member, Role } from "@/lib/api/types";
import { ROLE_LABELS, offered } from "@/lib/members";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: Member) => SortValue> = {
  name: (r) => ({ text: r.name }),
  email: (r) => ({ text: r.email }),
  role: (r) => ({ text: r.role }),
};

/**
 * The members of the organization with the actions the current role appears to allow. Presentation only: every
 * change is a request FastAPI decides from fresh rows. After ANY answer (success or refusal) the list is re-read
 * from the server, so a stale screen settles into the current truth; nothing is shown as done before the server
 * said so, and the last-owner rule is the server's.
 */
export function MembersAdmin({ members, actorRole }: { members: Member[]; actorRole: Role }) {
  const orgId = useOrgId();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "info" | "error"; text: string } | null>(null);
  const ownersListed = members.filter((member) => member.role === "owner").length;

  async function run(member: Member, call: () => Promise<ApiResult<unknown>>, done: string) {
    if (busy !== null) return;
    setBusy(member.id);
    setMessage(null);
    const result = await call();
    setMessage(result.ok ? { tone: "info", text: done } : { tone: "error", text: failureMessage(result.error) });
    router.refresh(); // authoritative state again, whatever the answer was
    setBusy(null);
  }

  const changeRole = (member: Member, role: Role) =>
    run(member, () => apiFetch(orgId, `/members/${encodeURIComponent(member.id)}`, { method: "PATCH", body: { role } }), `${member.name} is now ${ROLE_LABELS[role].toLowerCase()}.`);
  const remove = (member: Member) => run(member, () => apiFetch(orgId, `/members/${encodeURIComponent(member.id)}`, { method: "DELETE" }), `${member.name} was removed from the organization.`);

  const sorted = useSortedRows(members, SORT_COLUMNS);
  return (
    <div className="flex flex-col gap-4">
      {message && (
        <Notice tone={message.tone} testId="members-message">
          {message.text}
        </Notice>
      )}
      <table className="w-full max-w-3xl text-left text-sm" data-testid="members-table">
        <thead>
          <tr className="border-b border-zinc-300 dark:border-zinc-700">
            <SortHeader label="Name" {...sorted.header("name")} />
            <SortHeader label="Email" {...sorted.header("email")} />
            <SortHeader label="Role" {...sorted.header("role")} />
            <th className="py-2 font-medium">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.rows.map((member) => {
            const allowed = offered(actorRole, member, ownersListed);
            return (
              <tr key={member.id} data-testid="member-row" data-email={member.email} className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-2 pr-4">
                  {member.name}
                  {member.is_you && <span className="ml-2 text-zinc-500">(you)</span>}
                </td>
                <td className="py-2 pr-4">{member.email}</td>
                <td className="py-2 pr-4">
                  {allowed.roles.length > 0 ? (
                    <select
                      aria-label={`Role of ${member.name}`}
                      value={member.role}
                      disabled={busy !== null}
                      onChange={(event) => void changeRole(member, event.target.value as Role)}
                      className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900"
                      data-testid="member-role"
                    >
                      {/* the current role is always listed, so the control shows the truth even when it cannot be re-chosen */}
                      {[...new Set<Role>([member.role, ...allowed.roles])].map((role) => (
                        <option key={role} value={role}>
                          {ROLE_LABELS[role]}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span data-testid="member-role-text">{ROLE_LABELS[member.role]}</span>
                  )}
                </td>
                <td className="py-2">
                  {allowed.remove && (
                    <ConfirmButton
                      label="Remove"
                      confirmLabel="Remove member"
                      question={`Remove ${member.name} from this organization?`}
                      disabled={busy !== null}
                      onConfirm={() => void remove(member)}
                      testId="member-remove"
                    />
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
