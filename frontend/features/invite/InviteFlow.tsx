"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { usePreAuth } from "@/features/auth/use-pre-auth";
import { csrfHeader } from "@/lib/api/client";
import { PRE_AUTH_HEADER } from "@/lib/auth/cookies";
import { normalizeEmail, tokenFromFragment } from "@/lib/invite";

/**
 * Accept an organization invitation (`/invite#<token>`).
 *
 * THE SECRET. The token is in the URL fragment, which is never sent to a server. This component reads it ONCE in
 * the browser, removes it from the address and the history at once (`history.replaceState`), and keeps it only in a
 * ref (never in state that is rendered, never in a storage, a cookie, a query string or a redirect target). It is
 * sent only in the BODY of POST requests, together with the pre-auth double submit (before there is a session) or
 * the ordinary CSRF header (signed in). A reload cannot recover it: the invitee opens the link again.
 *
 * THE FLOW. Preview (what organization, which email, which role, does the account exist) -> either
 *   - already signed in as the invited email: join;
 *   - signed in as someone else: say so, offer to sign out HERE (the token stays in memory), then continue;
 *   - not signed in, account exists: sign in here (the protected login), then accept with the same in-memory token;
 *   - not signed in, no account: choose a name and a password, which creates the account, joins and signs in.
 * The browser never names the organization or the role: FastAPI takes both from the locked invitation row. After
 * success the page does a FULL navigation to `/o/{organization_id}`; nothing about it is remembered anywhere.
 */

interface Preview {
  organization_name: string;
  email: string;
  role: string;
  account_exists: boolean;
}

const INPUT = "rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900";

export function InviteFlow({ signedInEmail: initialSignedInEmail }: { signedInEmail: string | null }) {
  const preAuth = usePreAuth();
  const token = useRef<string | null>(null);
  const previewing = useRef(false);
  const [phase, setPhase] = useState<"reading" | "invalid" | "loading" | "ready">("reading");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [signedInEmail, setSignedInEmail] = useState(initialSignedInEmail);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    function capture(): boolean {
      const found = tokenFromFragment(window.location.hash);
      if (found === undefined) return false;
      token.current = found;
      // Out of the address bar and the history before anything else happens.
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      previewing.current = false;
      setMessage(null);
      setPreview(null);
      setPhase(found === null ? "invalid" : "loading");
      return true;
    }
    if (!capture() && token.current === null) setPhase("invalid"); // no link at all (or already consumed)
    window.addEventListener("hashchange", capture);
    return () => window.removeEventListener("hashchange", capture);
  }, []);

  useEffect(() => {
    // Ask what the invitation is for, once a pre-auth secret exists and a token is held.
    const secret = preAuth.current();
    const link = token.current;
    if (phase !== "loading" || !preAuth.ready || secret === null || link === null || previewing.current) return;
    previewing.current = true;
    void (async () => {
      try {
        const response = await fetch("/api/invite/preview", {
          method: "POST",
          headers: { "content-type": "application/json", [PRE_AUTH_HEADER]: secret },
          body: JSON.stringify({ token: link }),
          credentials: "same-origin",
          cache: "no-store",
        });
        if (response.ok) {
          setPreview((await response.json()) as Preview);
          setPhase("ready");
        } else {
          token.current = null;
          setPhase("invalid");
        }
      } catch {
        previewing.current = false;
        setMessage("Could not reach the server. Reload the page and open the link again.");
      }
    })();
  }, [phase, preAuth, preAuth.ready]);

  function finish(organizationId: string) {
    token.current = null;
    // A full page load on purpose: every server component must see the membership and the session.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/o/${encodeURIComponent(organizationId)}`);
  }

  async function accept() {
    const link = token.current;
    if (link === null) return;
    setWorking(true);
    setMessage(null);
    try {
      const response = await fetch("/api/invite/accept", {
        method: "POST",
        headers: { "content-type": "application/json", ...csrfHeader("POST") },
        body: JSON.stringify({ token: link }),
        credentials: "same-origin",
        cache: "no-store",
      });
      const body = (await response.json().catch(() => undefined)) as { organization_id?: string; detail?: { code?: string } } | undefined;
      if (response.ok && typeof body?.organization_id === "string") return finish(body.organization_id);
      if (response.status === 403 && body?.detail?.code === "invitation_wrong_account") {
        setMessage("This invitation was made for a different account. Sign out and continue as the invited account.");
        if (preview !== null && normalizeEmail(signedInEmail ?? "") === normalizeEmail(preview.email)) setSignedInEmail("");
      } else if (response.status === 404) {
        token.current = null;
        setPhase("invalid");
      } else if (response.status === 401) {
        setSignedInEmail(null);
        setMessage("Your session has ended. Sign in again to continue.");
      } else setMessage("Joining is unavailable right now. Try again.");
    } catch {
      setMessage("Could not reach the server. Check your connection and try again.");
    }
    setWorking(false);
  }

  async function signOutHere() {
    setWorking(true);
    setMessage(null);
    try {
      await fetch("/api/auth/logout", { method: "POST", headers: csrfHeader("POST"), credentials: "same-origin", cache: "no-store" });
      setSignedInEmail(null); // the browser is signed out whatever the answer; the token is still in memory
      await preAuth.refresh();
    } catch {
      setMessage("Could not sign out. Try again.");
    }
    setWorking(false);
  }

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const secret = preAuth.current();
    if (secret === null || preview === null) return;
    const element = event.currentTarget;
    const password = String(new FormData(element).get("password") ?? "");
    setWorking(true);
    setMessage(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json", [PRE_AUTH_HEADER]: secret },
        // The email is the invitation's: the person cannot sign in here as anyone else. `next` is irrelevant and sent as nothing.
        body: JSON.stringify({ email: preview.email, password }),
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.ok) {
        setSignedInEmail(preview.email);
        await accept(); // the same in-memory token; it never went through a redirect
        return;
      }
      if (response.status === 401) setMessage("Invalid email or password.");
      else if (response.status === 429) setMessage("Too many attempts. Try again later.");
      else if (response.status === 403) {
        setMessage("This page has expired. Try again.");
        await preAuth.refresh();
      } else setMessage("Sign-in is unavailable right now.");
    } catch {
      setMessage("Could not reach the server. Check your connection and try again.");
    }
    const field = element.elements.namedItem("password");
    if (field instanceof HTMLInputElement) field.value = "";
    setWorking(false);
  }

  async function createAccount(event: FormEvent<HTMLFormElement>) {
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
      const response = await fetch("/api/invite/accept-new", {
        method: "POST",
        headers: { "content-type": "application/json", [PRE_AUTH_HEADER]: secret },
        body: JSON.stringify({ token: link, name: String(form.get("name") ?? ""), password }),
        credentials: "same-origin",
        cache: "no-store",
      });
      const body = (await response.json().catch(() => undefined)) as { next?: string; detail?: { code?: string; message?: string } } | undefined;
      if (response.ok && typeof body?.next === "string" && body.next.startsWith("/o/")) {
        token.current = null;
        window.location.assign(body.next);
        return;
      }
      if (response.status === 422) setMessage(body?.detail?.message ?? "That password is not acceptable."); // nothing was consumed
      else if (response.status === 409) {
        setPreview((current) => (current === null ? current : { ...current, account_exists: true }));
        setMessage("An account with this email already exists. Sign in to accept the invitation.");
      } else if (response.status === 404) {
        token.current = null;
        setPhase("invalid");
      } else if (response.status === 403) {
        setMessage("This page has expired. Try again.");
        await preAuth.refresh();
      } else if (response.status === 429) setMessage("Too many attempts. Try again later.");
      else setMessage("Creating the account is unavailable right now.");
    } catch {
      setMessage("Could not reach the server. Check your connection and try again.");
    }
    setWorking(false);
  }

  if (phase === "reading") return null;
  if (phase === "invalid") {
    return (
      <Notice tone="error" testId="invite-invalid">
        This invitation is invalid or has expired. Ask the person who invited you for a new link.
      </Notice>
    );
  }
  if (phase === "loading" || preview === null) {
    return message ? <Notice tone="error" testId="invite-error">{message}</Notice> : <p className="text-sm text-zinc-500">Checking the invitation…</p>;
  }

  const matches = signedInEmail !== null && normalizeEmail(signedInEmail) === normalizeEmail(preview.email);
  return (
    <div className="flex flex-col gap-4" data-testid="invite-ready" data-ready={preAuth.ready || undefined}>
      <p className="text-sm">
        You have been invited to <strong data-testid="invite-organization">{preview.organization_name}</strong> as{" "}
        <strong data-testid="invite-role">{preview.role}</strong>, for <span data-testid="invite-email">{preview.email}</span>.
      </p>
      {message && <Notice tone="error" testId="invite-error">{message}</Notice>}

      {signedInEmail !== null && matches && (
        <div>
          <Button type="button" disabled={working} onClick={() => void accept()} data-testid="invite-join">
            {working ? "Joining…" : `Join ${preview.organization_name}`}
          </Button>
        </div>
      )}

      {signedInEmail !== null && !matches && (
        <div className="flex flex-col gap-3">
          <Notice testId="invite-wrong-account">
            You are signed in as <span data-testid="invite-signed-in-as">{signedInEmail || "another account"}</span>, but this invitation is for {preview.email}.
          </Notice>
          <div>
            <Button type="button" disabled={working} onClick={() => void signOutHere()} data-testid="invite-sign-out">
              Sign out and continue as {preview.email}
            </Button>
          </div>
        </div>
      )}

      {signedInEmail === null && preview.account_exists && (
        <form onSubmit={(event) => void signIn(event)} className="flex flex-col gap-3" data-testid="invite-sign-in-form">
          <p className="text-sm">Sign in as {preview.email} to accept.</p>
          <label className="flex flex-col gap-1 text-sm">
            Password
            <input name="password" type="password" autoComplete="current-password" required className={INPUT} />
          </label>
          <div>
            <Button type="submit" disabled={working || !preAuth.ready} data-testid="invite-sign-in">
              {working ? "Signing in…" : "Sign in and join"}
            </Button>
          </div>
        </form>
      )}

      {signedInEmail === null && !preview.account_exists && (
        <form onSubmit={(event) => void createAccount(event)} className="flex flex-col gap-3" data-testid="invite-create-form">
          <p className="text-sm">Create your account to join. Your email is {preview.email}.</p>
          <label className="flex flex-col gap-1 text-sm">
            Your name
            <input name="name" type="text" autoComplete="name" required maxLength={255} className={INPUT} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Password
            <input name="password" type="password" autoComplete="new-password" required className={INPUT} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Repeat the password
            <input name="confirm" type="password" autoComplete="new-password" required className={INPUT} />
          </label>
          <div>
            <Button type="submit" disabled={working || !preAuth.ready} data-testid="invite-create">
              {working ? "Creating…" : "Create account and join"}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
