"use client";

import type { Invitation } from "@/lib/api/types";
import { ROLE_LABELS } from "@/lib/members";
import { formatTimestamp } from "@/lib/timestamps";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: Invitation) => SortValue> = {
  email: (r) => ({ text: r.email }),
  role: (r) => ({ text: r.role }),
  invited: (r) => ({ text: r.created_at }),
  outcome: (r) => ({ text: r.state }),
};

/**
 * Invitations that ended: who invited whom, as what, when, and how it ended (accepted by whom and when, or revoked
 * when; a regenerated or superseded invitation is a revoked one). Read-only; the backend keeps every row.
 */
export function InvitationHistory({ invitations, timeZone }: { invitations: Invitation[]; timeZone: string | null }) {
  const sorted = useSortedRows(invitations, SORT_COLUMNS);
  if (invitations.length === 0) return null;
  return (
    <section aria-label="Invitation history" className="flex flex-col gap-2" data-testid="invitation-history">
      <h2 className="text-lg font-semibold">Invitation history</h2>
      <table className="w-full max-w-4xl text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-300 dark:border-zinc-700">
            <SortHeader label="Email" {...sorted.header("email")} />
            <SortHeader label="Role" {...sorted.header("role")} />
            <SortHeader label="Invited" {...sorted.header("invited")} />
            <SortHeader label="Outcome" {...sorted.header("outcome")} last />
          </tr>
        </thead>
        <tbody>
          {sorted.rows.map((invitation) => (
            <tr key={invitation.id} data-testid="invitation-history-row" data-email={invitation.email} className="border-b border-zinc-200 dark:border-zinc-800">
              <td className="py-2 pr-4">{invitation.email}</td>
              <td className="py-2 pr-4">{ROLE_LABELS[invitation.role]}</td>
              <td className="py-2 pr-4">
                {formatTimestamp(invitation.created_at, timeZone)}
                {invitation.invited_by_name && ` by ${invitation.invited_by_name}`}
              </td>
              <td className="py-2" data-testid="invitation-outcome">
                {invitation.state === "accepted"
                  ? `Accepted ${invitation.accepted_at ? formatTimestamp(invitation.accepted_at, timeZone) : ""}${invitation.accepted_by_name ? ` by ${invitation.accepted_by_name}` : ""}`
                  : `Revoked ${invitation.revoked_at ? formatTimestamp(invitation.revoked_at, timeZone) : ""}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
