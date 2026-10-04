"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { usePreAuth } from "@/features/auth/use-pre-auth";
import { PRE_AUTH_HEADER, isToken } from "@/lib/auth/cookies";

/**
 * Set a password with a single-use setup link (`/setup#<token>`): the first user, or recovery.
 *
 * The link secret lives in the URL FRAGMENT, which is never sent to a server. This page reads it ONCE, at once
 * removes it from the address and the history (`history.replaceState`), keeps it only in a ref (not in state,
 * not in any storage, not in the URL), never shows it again, and sends it only in the body of the POST. A
 * request the server cannot use (a weak password) keeps the link; an invalid, used or expired link is dropped
 * and reported with one generic message.
 */
export function SetupForm() {
  const preAuth = usePreAuth();
  const token = useRef<string | null>(null);
  const [hasLink, setHasLink] = useState<boolean | null>(null);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    // Take a new link from the fragment. Opening another link in the SAME tab only changes the fragment (no page
    // load), so this runs again on `hashchange`. An empty fragment means there is nothing new (also the case right
    // after we removed it).
    function capture(): boolean {
      const fragment = window.location.hash.replace(/^#/, "");
      if (fragment === "") return false;
      token.current = isToken(fragment) ? fragment : null;
      // Remove the secret from the address bar and the history immediately, before anything else happens.
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      setMessage(null);
      setHasLink(token.current !== null);
      return true;
    }
    if (!capture()) setHasLink(token.current !== null); // (React may run this effect twice in development)
    window.addEventListener("hashchange", capture);
    return () => window.removeEventListener("hashchange", capture);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const secret = preAuth.current();
    const link = token.current;
    if (secret === null || link === null) return;
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    if (password !== String(form.get("confirm") ?? "")) {
      setMessage("The two passwords do not match.");
      return;
    }
    setWorking(true);
    setMessage(null);
    try {
      const response = await fetch("/api/auth/setup", {
        method: "POST",
        headers: { "content-type": "application/json", [PRE_AUTH_HEADER]: secret },
        body: JSON.stringify({ token: link, password }),
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.ok) {
        token.current = null;
        // A full page load on purpose: every server component must see the new session.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign("/");
        return;
      }
      const body = (await response.json().catch(() => undefined)) as { detail?: { code?: string; message?: string } } | undefined;
      if (response.status === 400) {
        token.current = null; // an invalid, used or expired link cannot be retried
        setHasLink(false);
      } else if (response.status === 422) setMessage(body?.detail?.message ?? "That password is not acceptable.");
      else if (response.status === 429) setMessage("Too many attempts. Try again later.");
      else if (response.status === 403) {
        setMessage("This page has expired. Try again.");
        await preAuth.refresh();
      } else setMessage("Setting a password is unavailable right now.");
    } catch {
      setMessage("Could not reach the server. Check your connection and try again.");
    }
    setWorking(false);
  }

  if (hasLink === null) return null;
  if (!hasLink) {
    return (
      <Notice tone="error" testId="setup-invalid">
        This link is invalid or has expired. Ask for a new one.
      </Notice>
    );
  }
  return (
    <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-3" data-testid="setup-form" data-ready={preAuth.ready || undefined}>
      {message && <Notice tone="error" testId="setup-error">{message}</Notice>}
      <label className="flex flex-col gap-1 text-sm">
        New password
        <input name="password" type="password" autoComplete="new-password" required className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Repeat the password
        <input name="confirm" type="password" autoComplete="new-password" required className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
      </label>
      <div>
        <Button type="submit" disabled={working || !preAuth.ready} data-testid="setup-submit">
          {working ? "Saving…" : "Set password and sign in"}
        </Button>
      </div>
    </form>
  );
}
