"use client";

import { useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { CSRF_HEADER, readCsrfToken } from "@/lib/auth/cookies";

type Outcome = { tone: "success" | "error"; text: string } | null;

/**
 * Current password, new password, the new one again. Whether the new password is acceptable is the backend's
 * rule (its message is shown); the only local check is that the two new entries are the same. On success every
 * other session of this person is ended by FastAPI; this one stays signed in.
 */
export function ChangePasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const inFlight = useRef(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    if (next !== again) {
      setOutcome({ tone: "error", text: "The new password and its repetition are not the same." });
      return;
    }
    inFlight.current = true;
    setPending(true);
    setOutcome(null);
    try {
      const token = readCsrfToken(document.cookie);
      const response = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "content-type": "application/json", ...(token === null ? {} : { [CSRF_HEADER]: token }) },
        credentials: "same-origin",
        cache: "no-store",
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const body = (await response.json().catch(() => null)) as { message?: unknown } | null;
      if (response.ok) {
        setCurrent("");
        setNext("");
        setAgain("");
        setOutcome({ tone: "success", text: "Password changed. You stay signed in here; every other session was signed out." });
      } else {
        setOutcome({ tone: "error", text: typeof body?.message === "string" ? body.message : "The password could not be changed." });
      }
    } catch {
      setOutcome({ tone: "error", text: "The password could not be changed (no answer). Try signing in with the new password before changing it again." });
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  const field = "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900";
  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Change password">
      <label className="flex flex-col gap-1 text-sm font-medium">
        Current password
        <input type="password" name="current_password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required className={field} />
      </label>
      <label className="flex flex-col gap-1 text-sm font-medium">
        New password
        <input type="password" name="new_password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required className={field} />
      </label>
      <label className="flex flex-col gap-1 text-sm font-medium">
        New password again
        <input type="password" name="new_password_again" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required className={field} />
      </label>
      {outcome && (
        <Notice tone={outcome.tone === "error" ? "error" : undefined} testId={outcome.tone === "error" ? "password-error" : "password-changed"}>
          {outcome.text}
        </Notice>
      )}
      <div>
        <Button type="submit" disabled={pending} data-testid="change-password">
          {pending ? "Changing…" : "Change password"}
        </Button>
      </div>
    </form>
  );
}
