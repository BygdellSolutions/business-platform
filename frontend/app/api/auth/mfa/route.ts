import type { NextRequest } from "next/server";

import { callOwnAuth, json, problem } from "@/lib/auth/handlers";
import { parseMfaStatus } from "@/lib/auth/own-mfa";
import { instrument } from "@/lib/observability";

/** Whether the signed-in person has a second factor, whether their role requires one, and how many recovery codes are left. */
export const GET = instrument(async function GET(request: NextRequest) {
  const result = await callOwnAuth(request, "/api/auth/mfa", "GET");
  if ("refused" in result) return result.refused;
  const { upstream } = result;
  if (upstream.status === 401)
    return problem(401, "unauthenticated", "Sign in again.");
  const status =
    upstream.status === 200
      ? parseMfaStatus(await upstream.json().catch(() => null))
      : null;
  return status === null
    ? problem(502, "unavailable", "The second factor is unavailable right now.")
    : json({ status });
});
