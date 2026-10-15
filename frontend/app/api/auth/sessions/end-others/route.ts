import type { NextRequest } from "next/server";

import { callOwnSessions, json, problem } from "@/lib/auth/handlers";
import { instrument } from "@/lib/observability";

/** Sign out everywhere else: every session of the signed-in person except this browser's. */
export const POST = instrument(async function POST(request: NextRequest) {
  const result = await callOwnSessions(request, "/api/auth/sessions/end-others", "POST");
  if ("refused" in result) return result.refused;
  const { upstream } = result;
  if (upstream.status === 401) return problem(401, "unauthenticated", "Sign in again.");
  const body = (await upstream.json().catch(() => null)) as { ended?: unknown } | null;
  if (upstream.status !== 200 || typeof body?.ended !== "number") return problem(502, "unavailable", "The other sessions could not be ended right now.");
  return json({ ended: body.ended });
});
