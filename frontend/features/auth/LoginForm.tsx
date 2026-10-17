"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { usePreAuth } from "@/features/auth/use-pre-auth";
import { PRE_AUTH_HEADER } from "@/lib/auth/cookies";

const NOTICES: Record<string, string> = {
  "signed-out": "You have been signed out.",
  "signed-out-unconfirmed": "You are signed out of this browser. We could not confirm that the server ended the session; it ends by itself when it expires.",
  "session-ended": "Your session has ended. Sign in again.",
};

/**
 * The real login. Deliberately minimal: email, password, and one generic failure message (the backend and the
 * BFF never say whether an account exists). The password is read from the form when it is submitted and is not
 * kept in state. After success the BFF has set the protected cookies and returned a validated relative
 * destination; the page then does a full load so every server component sees the new session.
 *
 * A person with a second factor gets no session from the password alone: the BFF answers with a short-lived challenge, and
 * the page asks for the code (from the authenticator app, or a recovery code). The challenge lives only in this component's
 * state; it is not a session and is spent once.
 */
export function LoginForm({ next, notice }: { next: string; notice?: string }) {
  const preAuth = usePreAuth();
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<string | null>(null);

  function arrive(body: { next?: unknown } | undefined) {
    // A full page load on purpose: every server component must see the new session.
    window.location.assign(typeof body?.next === "string" && body.next.startsWith("/") && !body.next.startsWith("//") ? body.next : "/");
  }

  function explain(status: number) {
    if (status === 429) setMessage("Too many attempts. Try again later.");
    else if (status === 503) setMessage("The service is busy. Try again in a moment.");
    else setMessage("Sign-in is unavailable right now.");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const secret = preAuth.current();
    if (secret === null) return;
    const element = event.currentTarget; // (null after the first await)
    const form = new FormData(element);
    setWorking(true);
    setMessage(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json", [PRE_AUTH_HEADER]: secret },
        body: JSON.stringify({ email: String(form.get("email") ?? ""), password: String(form.get("password") ?? ""), next }),
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.ok) {
        const body = (await response.json().catch(() => undefined)) as { next?: unknown; mfa_required?: unknown; challenge?: unknown } | undefined;
        if (body?.mfa_required === true && typeof body.challenge === "string") {
          setChallenge(body.challenge);
          setWorking(false);
          return;
        }
        arrive(body);
        return;
      }
      if (response.status === 401) setMessage("Invalid email or password.");
      else if (response.status === 403) {
        setMessage("This page has expired. Try again.");
        await preAuth.refresh();
      } else explain(response.status);
    } catch {
      setMessage("Could not reach the server. Check your connection and try again.");
    }
    const password = element.elements.namedItem("password");
    if (password instanceof HTMLInputElement) password.value = "";
    setWorking(false);
  }

  async function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const secret = preAuth.current();
    if (secret === null || challenge === null) return;
    const element = event.currentTarget;
    const form = new FormData(element);
    setWorking(true);
    setMessage(null);
    try {
      const response = await fetch("/api/auth/login/mfa", {
        method: "POST",
        headers: { "content-type": "application/json", [PRE_AUTH_HEADER]: secret },
        body: JSON.stringify({ challenge, code: String(form.get("code") ?? ""), next }),
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.ok) {
        arrive((await response.json().catch(() => undefined)) as { next?: unknown } | undefined);
        return;
      }
      if (response.status === 401) setMessage("That code is not correct, or it has expired. Try again, or start over.");
      else if (response.status === 403) {
        setMessage("This page has expired. Start over.");
        await preAuth.refresh();
      } else explain(response.status);
    } catch {
      setMessage("Could not reach the server. Check your connection and try again.");
    }
    const code = element.elements.namedItem("code");
    if (code instanceof HTMLInputElement) code.value = "";
    setWorking(false);
  }

  if (challenge !== null) {
    return (
      <form onSubmit={(event) => void submitCode(event)} className="flex flex-col gap-3" data-testid="mfa-form" data-ready={preAuth.ready || undefined}>
        {message && <Notice tone="error" testId="mfa-error">{message}</Notice>}
        <p className="text-sm">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
        <label className="flex flex-col gap-1 text-sm">
          Code
          <input name="code" type="text" inputMode="numeric" autoComplete="one-time-code" autoFocus required maxLength={64} className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={working || !preAuth.ready} data-testid="mfa-submit">
            {working ? "Checking…" : "Continue"}
          </Button>
          <Button type="button" disabled={working} onClick={() => { setChallenge(null); setMessage(null); }} data-testid="mfa-start-over">
            Start over
          </Button>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-3" data-testid="login-form" data-ready={preAuth.ready || undefined}>
      {notice && NOTICES[notice] && <Notice testId="login-notice">{NOTICES[notice]}</Notice>}
      {message && <Notice tone="error" testId="login-error">{message}</Notice>}
      {preAuth.failed && <Notice tone="error" testId="login-unavailable">The sign-in page could not be prepared. Reload it.</Notice>}
      <label className="flex flex-col gap-1 text-sm">
        Email
        <input name="email" type="email" autoComplete="username" required className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Password
        <input name="password" type="password" autoComplete="current-password" required className="rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900" />
      </label>
      <div>
        <Button type="submit" disabled={working || !preAuth.ready} data-testid="login-submit">
          {working ? "Signing in…" : "Sign in"}
        </Button>
      </div>
    </form>
  );
}
