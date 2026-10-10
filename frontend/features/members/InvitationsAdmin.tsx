"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { ApiError, ApiResult } from "@/lib/api/errors";
import type { Invitation, InvitationCreated, Role } from "@/lib/api/types";
import { inviteLink } from "@/lib/invite";
import { ADMIN_MANAGEABLE, ALL_ROLES, ROLE_LABELS } from "@/lib/members";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: Invitation) => SortValue> = {
  email: (r) => ({ text: r.email }),
  role: (r) => ({ text: r.role }),
  invited_by: (r) => ({ text: r.invited_by_name ?? null }),
  expires: (r) => ({ text: r.expires_at }),
};

/** The roles an inviter is OFFERED (presentation only: FastAPI re-decides from fresh rows). */
export function invitableRoles(actorRole: Role): readonly Role[] {
  return actorRole === "owner" ? ALL_ROLES : actorRole === "admin" ? ADMIN_MANAGEABLE : [];
}

export function canManageInvitation(actorRole: Role, invitationRole: Role): boolean {
  return invitableRoles(actorRole).includes(invitationRole);
}

function failure(error: ApiError): string {
  switch (error.kind) {
    case "conflict":
      if (error.message.includes("already belongs")) return "That person already belongs to this organization.";
      if (error.message.includes("pending invitation")) return "There is already a pending invitation for that email. Regenerate it to replace it.";
      if (error.message.includes("already accepted")) return "That invitation was already accepted. The list has been refreshed.";
      return "That conflicts with the current state. The list has been refreshed.";
    case "forbidden":
      return "You are not allowed to do that, or your role changed. The list has been refreshed.";
    case "not_found":
      return "That invitation no longer exists, or you no longer have access to it. The list has been refreshed.";
    case "validation":
      return "Enter a valid email address and a role.";
    case "network":
      return "Could not reach the server. Nothing was changed that we know of.";
    default:
      return "Something went wrong. The list has been refreshed.";
  }
}

/**
 * Pending invitations of the organization, for owners and admins. The link is shown ONCE, right after it is made
 * (or regenerated): it exists only in this component's memory, cannot be fetched again (the list never carries
 * a token) and disappears on reload or navigation. There is no email delivery: the administrator copies the link.
 * Everything here is presentation; every request is decided by FastAPI.
 */
export function InvitationsAdmin({ invitations, actorRole }: { invitations: Invitation[]; actorRole: Role }) {
  const orgId = useOrgId();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [shown, setShown] = useState<{ email: string; link: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const roles = invitableRoles(actorRole);

  async function run(call: () => Promise<ApiResult<unknown>>, onSuccess: (data: unknown) => void) {
    if (busy) return;
    setBusy(true);
    setMessage(null);
    const result = await call();
    if (result.ok) onSuccess(result.data);
    else setMessage(failure(result.error));
    router.refresh(); // the list is always re-read from the server
    setBusy(false);
  }

  function show(created: InvitationCreated) {
    setCopied(false);
    setShown({ email: created.email, link: inviteLink(window.location.origin, created.token) });
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const element = event.currentTarget;
    const form = new FormData(element);
    const body = { email: String(form.get("email") ?? "").trim(), role: String(form.get("role") ?? "") };
    await run(
      () => apiFetch<InvitationCreated>(orgId, "/invitations", { method: "POST", body }),
      (data) => {
        show(data as InvitationCreated);
        element.reset();
      },
    );
  }

  const regenerate = (invitation: Invitation) => run(() => apiFetch<InvitationCreated>(orgId, `/invitations/${encodeURIComponent(invitation.id)}/regenerate`, { method: "POST" }), (data) => show(data as InvitationCreated));
  const revoke = (invitation: Invitation) =>
    run(
      () => apiFetch(orgId, `/invitations/${encodeURIComponent(invitation.id)}`, { method: "DELETE" }),
      () => {
        setShown((current) => (current?.email === invitation.email ? null : current));
        setMessage(`The invitation for ${invitation.email} was revoked.`);
      },
    );

  async function copy() {
    if (shown === null) return;
    try {
      await navigator.clipboard.writeText(shown.link);
      setCopied(true);
    } catch {
      setCopied(false); // the link is selectable in the box; copying by hand works
    }
  }

  const sorted = useSortedRows(invitations, SORT_COLUMNS);
  return (
    <section className="flex flex-col gap-4" aria-labelledby="invitations-heading" data-testid="invitations">
      <h2 id="invitations-heading" className="text-xl font-semibold">
        Invitations
      </h2>

      {shown && (
        <Notice testId="invitation-link-panel">
          <div className="flex flex-col gap-2">
            <p>
              Invitation link for <strong>{shown.email}</strong>. <strong>It is shown only now and cannot be retrieved again</strong> (regenerate the invitation to get a new one). Anyone with the link can use it.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <input readOnly value={shown.link} aria-label="Invitation link" onFocus={(event) => event.currentTarget.select()} className="min-w-0 flex-1 rounded border border-zinc-400 px-2 py-1 font-mono text-xs dark:bg-zinc-900" data-testid="invitation-link" />
              <Button type="button" onClick={() => void copy()} data-testid="invitation-copy">
                {copied ? "Copied" : "Copy link"}
              </Button>
              <Button type="button" onClick={() => setShown(null)} data-testid="invitation-dismiss">
                Done
              </Button>
            </div>
          </div>
        </Notice>
      )}
      {message && (
        <Notice tone={message.startsWith("The invitation for") ? "info" : "error"} testId="invitations-message">
          {message}
        </Notice>
      )}

      <form onSubmit={(event) => void create(event)} className="flex flex-wrap items-end gap-3" data-testid="invite-form">
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input name="email" type="email" required autoComplete="off" className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Role
          <select name="role" defaultValue="employee" className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" data-testid="invite-role-select">
            {roles.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABELS[role]}
              </option>
            ))}
          </select>
        </label>
        <Button type="submit" disabled={busy} data-testid="invite-submit">
          Create invitation
        </Button>
      </form>

      {invitations.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-invitations">
          No pending invitations.
        </p>
      ) : (
        <table className="w-full max-w-3xl text-left text-sm" data-testid="invitations-table">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <SortHeader label="Email" {...sorted.header("email")} />
              <SortHeader label="Role" {...sorted.header("role")} />
              <SortHeader label="Invited by" {...sorted.header("invited_by")} />
              <SortHeader label="Expires" {...sorted.header("expires")} />
              <th className="py-2 font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.rows.map((invitation) => (
              <tr key={invitation.id} data-testid="invitation-row" data-email={invitation.email} className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-2 pr-4">{invitation.email}</td>
                <td className="py-2 pr-4">{ROLE_LABELS[invitation.role]}</td>
                <td className="py-2 pr-4" data-testid="invitation-invited">
                  {invitation.invited_by_name ?? <span className="text-zinc-500">not recorded</span>}
                  <span className="block text-xs text-zinc-500">{new Date(invitation.created_at).toLocaleDateString("sv-SE")}</span>
                </td>
                <td className="py-2 pr-4" data-testid="invitation-state">
                  {invitation.state === "expired" ? "Expired" : new Date(invitation.expires_at).toLocaleDateString("sv-SE")}
                </td>
                <td className="flex flex-wrap gap-2 py-2">
                  {canManageInvitation(actorRole, invitation.role) && (
                    <>
                      <Button type="button" disabled={busy} onClick={() => void regenerate(invitation)} data-testid="invitation-regenerate">
                        Regenerate
                      </Button>
                      <ConfirmButton
                        label="Revoke"
                        confirmLabel="Revoke invitation"
                        question={`Revoke the invitation for ${invitation.email}?`}
                        disabled={busy}
                        onConfirm={() => void revoke(invitation)}
                        testId="invitation-revoke"
                      />
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
