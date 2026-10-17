"use client";

import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { CSRF_HEADER, readCsrfToken } from "@/lib/auth/cookies";
import { describeBrowser, type OwnSession } from "@/lib/auth/own-sessions";
import { formatTimestamp } from "@/lib/timestamps";

type Outcome = { tone: "success" | "error"; text: string } | null;

/**
 * Where this person is signed in, and the ways to end it: one session, every other session, or all of them (this browser too).
 * The list is FastAPI's, scoped to the caller; this panel only shows it and asks. After ending sessions the list is read again,
 * so what is shown is what the server has.
 */
export function SessionsPanel({ initial }: { initial: OwnSession[] }) {
  const router = useRouter();
  const [sessions, setSessions] = useState<OwnSession[]>(initial);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/auth/sessions", {
        credentials: "same-origin",
        cache: "no-store",
      });
      const body = (await response.json().catch(() => null)) as {
        sessions?: OwnSession[];
      } | null;
      if (response.ok && Array.isArray(body?.sessions))
        setSessions(body.sessions);
      else
        setOutcome({
          tone: "error",
          text: "Your sessions could not be loaded.",
        });
    } catch {
      setOutcome({ tone: "error", text: "Your sessions could not be loaded." });
    }
  }, []);

  async function act(
    path: string,
    method: "POST" | "DELETE",
    done: string,
    everywhere = false,
  ) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setOutcome(null);
    try {
      const token = readCsrfToken(document.cookie);
      const response = await fetch(path, {
        method,
        headers: token === null ? {} : { [CSRF_HEADER]: token },
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        setOutcome({
          tone: "error",
          text: "That did not work. Nothing was changed that you can rely on; reload the list and try again.",
        });
        return;
      }
      if (everywhere) {
        router.push("/login");
        return;
      }
      setOutcome({ tone: "success", text: done });
      await load();
    } catch {
      setOutcome({
        tone: "error",
        text: "No answer from the server. Reload the list to see what is still signed in.",
      });
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  const others = sessions.filter((session) => !session.current);
  return (
    <div className="flex flex-col gap-3" data-testid="sessions-panel">
      {outcome && (
        <Notice
          tone={outcome.tone === "error" ? "error" : undefined}
          testId={outcome.tone === "error" ? "sessions-error" : "sessions-done"}
        >
          {outcome.text}
        </Notice>
      )}
      <div className="overflow-x-auto">
        <table
          className="w-full text-sm whitespace-nowrap"
          data-testid="sessions-table"
        >
          <thead>
            <tr className="text-left text-zinc-500">
              <th className="py-1 pr-4 font-medium">Browser</th>
              <th className="py-1 pr-4 font-medium">Signed in</th>
              <th className="py-1 pr-4 font-medium">Last active</th>
              <th className="py-1 font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr
                key={session.id}
                data-testid="session-row"
                data-current={session.current ? "true" : "false"}
              >
                <td className="py-1 pr-4">
                  {describeBrowser(session.userAgent)}
                  {session.current && (
                    <span className="ml-2 rounded bg-accent/10 px-1.5 py-0.5 text-xs font-medium text-accent">
                      This browser
                    </span>
                  )}
                </td>
                <td className="py-1 pr-4">
                  {formatTimestamp(session.createdAt, null)}
                </td>
                <td className="py-1 pr-4">
                  {formatTimestamp(session.lastUsedAt, null)}
                </td>
                <td className="py-1 text-right">
                  {!session.current && (
                    <Button
                      type="button"
                      disabled={pending}
                      onClick={() =>
                        void act(
                          `/api/auth/sessions/${session.id}`,
                          "DELETE",
                          "That session was signed out.",
                        )
                      }
                      data-testid="end-session"
                    >
                      Sign out
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          disabled={pending || others.length === 0}
          onClick={() =>
            void act(
              "/api/auth/sessions/end-others",
              "POST",
              "Every other session was signed out.",
            )
          }
          data-testid="end-others"
        >
          Sign out all other sessions
        </Button>
        <Button
          type="button"
          variant="danger"
          disabled={pending}
          onClick={() =>
            void act("/api/auth/sessions/end-all", "POST", "", true)
          }
          data-testid="end-all"
        >
          Sign out everywhere
        </Button>
      </div>
    </div>
  );
}
