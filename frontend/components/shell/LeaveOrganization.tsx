"use client";

import { useState } from "react";

import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";

/**
 * Leave THIS organization (any role). It is separate from administrative removal on purpose: it asks for no
 * administrator authority and has its own endpoint. The last owner is refused by the server (shown, nothing
 * navigates). On success, and when the server says the membership is already gone, the browser goes back to the
 * ordinary organization selection: the person stays signed in and keeps their other organizations.
 */
export function LeaveOrganization({ orgId }: { orgId: string }) {
  const [message, setMessage] = useState<string | null>(null);

  async function leave() {
    setMessage(null);
    const result = await apiFetch(orgId, "/members/leave", { method: "POST" });
    if (result.ok || result.error.kind === "not_found") {
      // A full page load on purpose: nothing of the departed organization may survive in the page.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/");
      return;
    }
    if (result.error.kind === "conflict" && result.error.code === "last_owner") {
      setMessage("You are the last owner: make someone else an owner before leaving.");
    } else {
      setMessage("Could not leave the organization.");
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      <ConfirmButton label="Leave organization" confirmLabel="Leave" question="Leave this organization? You keep your account and other organizations." onConfirm={() => void leave()} testId="leave-organization" />
      {message && (
        <Notice tone="error" testId="leave-message">
          {message}
        </Notice>
      )}
    </span>
  );
}
