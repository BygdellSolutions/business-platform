import type { NextRequest } from "next/server";

import {
  callAuthEndpoint,
  json,
  parseSession,
  preAuthProblem,
  problem,
  readJsonObject,
  retryAfter,
  sessionModeOnly,
  signedIn,
} from "@/lib/auth/handlers";
import { isToken } from "@/lib/auth/cookies";
import { isTypedCode } from "@/lib/auth/own-mfa";
import { safeNext } from "@/lib/auth/safe-next";
import { instrument } from "@/lib/observability";

/**
 * Second step of a sign-in for a person with a second factor: the challenge the first step returned and a code (or a recovery
 * code). Same contract as login: Origin and the pre-auth pair are checked BEFORE FastAPI, every wrong way is the same 401, and the
 * answer carries a validated relative destination and never a token.
 */
export const POST = instrument(async function POST(request: NextRequest) {
  const refused = sessionModeOnly() ?? preAuthProblem(request);
  if (refused) return refused;

  const body = await readJsonObject(request);
  if (
    body === null ||
    typeof body.challenge !== "string" ||
    !isToken(body.challenge) ||
    !isTypedCode(body.code)
  ) {
    return problem(
      400,
      "invalid_request",
      "Enter the code from your authenticator app.",
    );
  }

  let upstream: Response;
  try {
    upstream = await callAuthEndpoint(request, "/api/auth/login/mfa", {
      challenge: body.challenge,
      code: body.code,
    });
  } catch {
    return problem(502, "unavailable", "Sign-in is unavailable right now.");
  }

  if (upstream.status === 200) {
    const session = parseSession(await upstream.json().catch(() => undefined));
    return session === null
      ? problem(502, "unavailable", "Sign-in is unavailable right now.")
      : signedIn(session, safeNext(body.next));
  }
  if (upstream.status === 401)
    return json({ detail: "Invalid or expired code" }, 401);
  if (upstream.status === 429)
    return problem(
      429,
      "throttled",
      "Too many attempts. Try again later.",
      retryAfter(upstream),
    );
  if (upstream.status === 503)
    return problem(
      503,
      "busy",
      "The service is busy. Try again in a moment.",
      retryAfter(upstream),
    );
  return problem(502, "unavailable", "Sign-in is unavailable right now.");
});
