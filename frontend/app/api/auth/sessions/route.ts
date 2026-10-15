import type { NextRequest } from "next/server";

import { callOwnSessions, json, problem } from "@/lib/auth/handlers";
import { parseSessionList } from "@/lib/auth/own-sessions";
import { instrument } from "@/lib/observability";

/** The signed-in person's own sessions (never anyone else's: FastAPI scopes the list to the caller). */
export const GET = instrument(async function GET(request: NextRequest) {
  const result = await callOwnSessions(request, "/api/auth/sessions", "GET");
  if ("refused" in result) return result.refused;
  const { upstream } = result;
  if (upstream.status === 401) return problem(401, "unauthenticated", "Sign in again.");
  if (upstream.status !== 200) return problem(502, "unavailable", "Your sessions are unavailable right now.");
  const sessions = parseSessionList(await upstream.json().catch(() => null));
  return sessions === null ? problem(502, "unavailable", "Your sessions are unavailable right now.") : json({ sessions });
});
