"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { CSRF_HEADER, readCsrfToken } from "@/lib/auth/cookies";

/**
 * Sign out. The shell chooses the variant once; nothing else knows which mode is active.
 *
 *   dev      the existing dev-session form
 *   session  a CSRF-protected POST to the BFF, which asks FastAPI to end the session and ALWAYS clears this
 *            browser's cookies. If the server's answer could not be confirmed the login page says so: the browser
 *            is signed out, but nobody should be told the server-side session was ended when we do not know.
 */
export function SignOut({ mode, variant = "button" }: { mode: "dev" | "session"; variant?: "button" | "menu" }) {
  if (mode === "dev") {
    return (
      <form action="/api/dev-session" method="post">
        <input type="hidden" name="logout" value="1" />
        {variant === "menu" ? (
          <button type="submit" role="menuitem" data-testid="sign-out" className={MENU_ITEM}>
            Sign out
          </button>
        ) : (
          <Button type="submit" variant="secondary" data-testid="sign-out">
            Sign out
          </Button>
        )}
      </form>
    );
  }
  return <SessionSignOut variant={variant} />;
}

/** The look of a sign-out entry in the account menu (a full-width row like the other entries). */
const MENU_ITEM = "flex w-full rounded-lg px-3 py-2 text-left text-sm text-secondary hover:bg-surface-hover hover:text-foreground disabled:opacity-50";

function SessionSignOut({ variant }: { variant: "button" | "menu" }) {
  const [working, setWorking] = useState(false);

  async function signOut() {
    setWorking(true);
    let target = "/login?notice=signed-out";
    try {
      const token = readCsrfToken(document.cookie);
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        headers: token === null ? {} : { [CSRF_HEADER]: token },
        credentials: "same-origin",
        cache: "no-store",
      });
      const body: unknown = await response.json().catch(() => undefined);
      const confirmed = typeof body === "object" && body !== null && (body as { revoked?: unknown }).revoked;
      if (!response.ok) target = "/login?notice=signed-out-unconfirmed";
      else if (confirmed === "unconfirmed") target = "/login?notice=signed-out-unconfirmed";
    } catch {
      target = "/login?notice=signed-out-unconfirmed";
    }
    // A full page load: no state of the signed-out session may survive in the page.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(target);
  }

  if (variant === "menu") {
    return (
      <button type="button" role="menuitem" disabled={working} onClick={() => void signOut()} data-testid="sign-out" className={MENU_ITEM}>
        {working ? "Signing out…" : "Sign out"}
      </button>
    );
  }
  return (
    <Button type="button" disabled={working} onClick={() => void signOut()} data-testid="sign-out">
      {working ? "Signing out…" : "Sign out"}
    </Button>
  );
}
