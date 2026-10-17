import { NextResponse, type NextRequest } from "next/server";

import { callOwnAuth, problem } from "@/lib/auth/handlers";
import { instrument } from "@/lib/observability";

const MAX_BYTES = 5 * 1024 * 1024;

/**
 * A copy of everything the platform holds about the signed-in person's own ACCOUNT, as a JSON download. Only FastAPI's answer to
 * THIS caller can come back (it is scoped to the authenticated user); the BFF checks that it is a JSON object of a sane size and
 * rebuilds the headers, so nothing else it said reaches the browser.
 */
export const GET = instrument(async function GET(request: NextRequest) {
  const result = await callOwnAuth(request, "/api/me/export", "GET");
  if ("refused" in result) return result.refused;
  const { upstream } = result;
  if (upstream.status === 401) return problem(401, "unauthenticated", "Sign in again.");
  if (upstream.status === 429) return problem(429, "throttled", "You have downloaded several copies recently. Try again in a while.");
  if (upstream.status !== 200) return problem(502, "unavailable", "Your data is unavailable right now.");

  const text = await upstream.text();
  let body: unknown = null;
  try {
    body = text.length <= MAX_BYTES ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (typeof body !== "object" || body === null || Array.isArray(body) || !("profile" in body)) {
    return problem(502, "unavailable", "Your data is unavailable right now.");
  }
  return new NextResponse(text, {
    status: 200,
    headers: {
      "content-type": "application/json",
      "content-disposition": 'attachment; filename="account-data.json"',
      "x-content-type-options": "nosniff",
      "cache-control": "private, no-store",
    },
  });
});
