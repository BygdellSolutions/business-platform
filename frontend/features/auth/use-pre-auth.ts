"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Ask the BFF for a pre-auth secret (it sets the matching HttpOnly cookie); null if it could not be made. */
async function fetchPreAuthSecret(): Promise<string | null> {
  try {
    const response = await fetch("/api/auth/pre", { cache: "no-store", credentials: "same-origin" });
    const body: unknown = await response.json();
    const token = typeof body === "object" && body !== null ? (body as { token?: unknown }).token : undefined;
    return response.ok && typeof token === "string" ? token : null;
  } catch {
    return null;
  }
}

/**
 * The pre-authentication double-submit secret for a page that acts before a session exists (login, setup).
 *
 * It is fetched from the BFF, which sets the matching HttpOnly cookie, and kept in a ref (not in state, not in
 * storage, not in the URL). It is NOT a credential: it identifies no one and the BFF only compares it with its
 * cookie. `refresh` gets a new one (after the BFF says it expired).
 */
export function usePreAuth() {
  const secret = useRef<string | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  const accept = useCallback((token: string | null) => {
    secret.current = token;
    setFailed(token === null);
    setReady(token !== null);
  }, []);

  const refresh = useCallback(async () => {
    setReady(false);
    accept(await fetchPreAuthSecret());
  }, [accept]);

  useEffect(() => {
    let current = true;
    void fetchPreAuthSecret().then((token) => {
      if (current) accept(token);
    });
    return () => {
      current = false;
    };
  }, [accept]);

  return { ready, failed, refresh, current: () => secret.current };
}
