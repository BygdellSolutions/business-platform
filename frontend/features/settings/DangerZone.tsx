"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent, type ReactNode } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { ApiError } from "@/lib/api/errors";
import type { Member, Role } from "@/lib/api/types";

/**
 * Leaving, transferring ownership and deleting the organization. Each asks for the password (FastAPI's "recent
 * authentication"); which actions are offered is a convenience, the backend decides everything from fresh rows.
 */
export function DangerZone({
  organizationName,
  role,
  members,
  passwordChecked,
}: {
  organizationName: string;
  role: Role | undefined;
  /** The organization's members (owners and admins can read them); empty for everyone else. */
  members: Member[];
  /** False in the development sign-in, which has no passwords (the field is then not checked). */
  passwordChecked: boolean;
}) {
  // Kept here, above the sections: after a transfer the page refreshes and the transfer section itself is gone
  // (the person is no longer an owner), but the confirmation must stay.
  const [transferred, setTransferred] = useState(false);
  const isOwner = role === "owner";
  const owners = members.filter((member) => member.role === "owner").length;
  const soleOwner = isOwner && owners <= 1;
  return (
    <section aria-label="Danger zone" data-testid="danger-zone" className="flex max-w-xl flex-col gap-5 rounded border-2 border-red-600 p-4">
      <h2 className="text-lg font-semibold text-red-700 dark:text-red-400">Danger zone</h2>
      {transferred && <Notice testId="transferred">Ownership transferred. You are now an admin.</Notice>}
      <LeaveSection soleOwner={soleOwner} passwordChecked={passwordChecked} />
      {isOwner && <TransferSection members={members.filter((member) => !member.is_you && member.role !== "owner")} passwordChecked={passwordChecked} onTransferred={() => setTransferred(true)} />}
      {isOwner && <DeleteSection organizationName={organizationName} passwordChecked={passwordChecked} />}
    </section>
  );
}

function useAction() {
  const orgId = useOrgId();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  async function run(path: string, body: Record<string, unknown>, explain: (error: ApiError) => string, doneWhen: ApiError["kind"][] = []): Promise<boolean> {
    if (inFlight.current) return false;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await apiFetch(orgId, path, { method: "POST", body });
      if (result.ok || doneWhen.includes(result.error.kind)) return true;
      setError(explain(result.error));
      return false;
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }
  return { pending, error, run };
}

function explainCommon(error: ApiError): string {
  if (error.kind === "forbidden" || error.kind === "client") return error.message;
  if (error.kind === "conflict" && error.code === "last_owner") return "You are the only owner. Transfer ownership to another member before leaving, or delete the organization.";
  if (error.kind === "network") return "No answer from the server. Reload the page to see what happened before trying again.";
  return error.message || "That did not work.";
}

function PasswordField({ value, onChange, checked, name }: { value: string; onChange: (value: string) => void; checked: boolean; name: string }) {
  return (
    <label className="flex flex-col gap-1 text-sm font-medium">
      Your password
      <input
        type="password"
        name={name}
        autoComplete="current-password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900"
      />
      {!checked && <span className="text-xs font-normal text-zinc-500">Development sign-in: not checked here.</span>}
    </label>
  );
}

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 border-t border-red-200 pt-4 first-of-type:border-t-0 first-of-type:pt-0 dark:border-red-900">
      <h3 className="font-medium">{title}</h3>
      {children}
    </div>
  );
}

function LeaveSection({ soleOwner, passwordChecked }: { soleOwner: boolean; passwordChecked: boolean }) {
  const { pending, error, run } = useAction();
  const [password, setPassword] = useState("");
  if (soleOwner) {
    return (
      <Part title="Leave organization">
        <Notice testId="sole-owner">
          You are the only owner of this organization. Transfer ownership to another member before leaving, or delete the organization.
        </Notice>
      </Part>
    );
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    // A membership that is already gone (404) counts as left: the organization is out of reach either way.
    if (await run("/members/leave", { password }, explainCommon, ["not_found"])) {
      // A full page load: nothing of the organization just left may stay in the page.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/");
    }
  }
  return (
    <Part title="Leave organization">
      <form onSubmit={submit} className="flex flex-col gap-2" aria-label="Leave organization">
        <p className="text-sm">You lose access to this organization. Your account and your other organizations stay.</p>
        <PasswordField value={password} onChange={setPassword} checked={passwordChecked} name="leave_password" />
        {error && <Notice tone="error" testId="leave-error">{error}</Notice>}
        <div>
          <Button type="submit" disabled={pending} data-testid="leave-organization">
            {pending ? "Leaving…" : "Leave organization"}
          </Button>
        </div>
      </form>
    </Part>
  );
}

function TransferSection({ members, passwordChecked, onTransferred }: { members: Member[]; passwordChecked: boolean; onTransferred: () => void }) {
  const router = useRouter();
  const { pending, error, run } = useAction();
  const [target, setTarget] = useState("");
  const [password, setPassword] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await run("/organization/transfer-ownership", { membership_id: target, password }, explainCommon)) {
      setPassword("");
      onTransferred();
      router.refresh();
    }
  }
  return (
    <Part title="Transfer ownership">
      {members.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-transfer-candidates">
          There is nobody to transfer ownership to yet. Invite a member first.
        </p>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-2" aria-label="Transfer ownership">
          <p className="text-sm">The member becomes an owner and you become an admin. You can leave afterwards.</p>
          <label className="flex flex-col gap-1 text-sm font-medium">
            New owner
            <select name="membership_id" value={target} onChange={(event) => setTarget(event.target.value)} className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900">
              <option value="">Choose a member</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.name} ({member.email}, {member.role})
                </option>
              ))}
            </select>
          </label>
          <PasswordField value={password} onChange={setPassword} checked={passwordChecked} name="transfer_password" />
          {error && <Notice tone="error" testId="transfer-error">{error}</Notice>}
          <div>
            <Button type="submit" disabled={pending || target === ""} data-testid="transfer-ownership">
              {pending ? "Transferring…" : "Transfer ownership"}
            </Button>
          </div>
        </form>
      )}
    </Part>
  );
}

function DeleteSection({ organizationName, passwordChecked }: { organizationName: string; passwordChecked: boolean }) {
  const { pending, error, run } = useAction();
  const [typed, setTyped] = useState("");
  const [password, setPassword] = useState("");
  const [understood, setUnderstood] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const explain = (failure: ApiError) =>
      failure.kind === "validation" ? "Type the organization's name exactly as shown." : explainCommon(failure);
    if (await run("/organization/delete", { confirm_name: typed, password }, explain)) {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/");
    }
  }
  return (
    <Part title="Delete organization">
      <form onSubmit={submit} className="flex flex-col gap-2" aria-label="Delete organization">
        <div className="text-sm" data-testid="delete-warning">
          <p className="font-semibold text-red-700 dark:text-red-400">This permanently deletes the organization and ALL of its data.</p>
          <p>
            Customers, horses, the catalog, orders, issued invoices and their PDFs, history, settings and every member&apos;s access are
            removed for good. It cannot be undone, and nobody can restore it from the app.
          </p>
          <p className="mt-1">
            Swedish bookkeeping law (bokföringslagen) normally requires keeping accounting records, including invoices, for seven years: keep
            your own copies of what you need before deleting.
          </p>
        </div>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Type the organization&apos;s name to confirm: <span className="font-mono">{organizationName}</span>
          <input name="confirm_name" value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900" />
        </label>
        <PasswordField value={password} onChange={setPassword} checked={passwordChecked} name="delete_password" />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" name="understood" checked={understood} onChange={(event) => setUnderstood(event.target.checked)} />
          I understand that everything is deleted permanently.
        </label>
        {error && <Notice tone="error" testId="delete-error">{error}</Notice>}
        <div>
          <Button type="submit" disabled={pending || !understood || typed === ""} data-testid="delete-organization" className="bg-red-700 text-white">
            {pending ? "Deleting…" : "Delete this organization permanently"}
          </Button>
        </div>
      </form>
    </Part>
  );
}
