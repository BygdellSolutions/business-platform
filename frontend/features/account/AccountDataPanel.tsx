"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";

type Outcome = { tone: "success" | "error"; text: string } | null;

/**
 * "Download my account data": a JSON copy of what the platform holds about this account (profile, organizations you belong to,
 * sessions, security events, the state of your second factor; never a password or a key). What an organization keeps in its own
 * records belongs to that organization, which says so in the copy itself.
 */
export function AccountDataPanel() {
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);

  async function download() {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setOutcome(null);
    try {
      const response = await fetch("/api/auth/account-export", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) {
        setOutcome({ tone: "error", text: response.status === 429 ? "You have downloaded several copies recently. Try again in a while." : "Your data could not be downloaded right now." });
        return;
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "account-data.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setOutcome({ tone: "success", text: "Downloaded account-data.json." });
    } catch {
      setOutcome({ tone: "error", text: "No answer from the server. Try again." });
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-3" data-testid="account-data-panel">
      <p className="text-sm text-zinc-500">A copy of what this platform holds about your account: profile, your organizations and roles, sessions, security events.</p>
      {outcome && (
        <Notice tone={outcome.tone === "error" ? "error" : undefined} testId={outcome.tone === "error" ? "account-data-error" : "account-data-done"}>
          {outcome.text}
        </Notice>
      )}
      <div>
        <Button type="button" disabled={pending} onClick={() => void download()} data-testid="download-account-data">
          {pending ? "Preparing…" : "Download my account data"}
        </Button>
      </div>
    </div>
  );
}
