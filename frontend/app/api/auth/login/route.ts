import type { NextRequest } from "next/server";

import { safeNext } from "@/lib/auth/safe-next";
import { callAuthEndpoint, json, parseSession, preAuthProblem, problem, readJsonObject, retryAfter, sessionModeOnly, signedIn } from "@/lib/auth/handlers";
import { instrument } from "@/lib/observability";

/**
 * Login. The browser sends the email and password (and where it wanted to go) with the pre-auth header; the BFF
 * validates Origin and the pre-auth pair, asks FastAPI, and on success sets the protected cookies. The response
 * to the browser carries a validated relative destination and NEVER a token. Every wrong credential gets the
 * same answer, taken from FastAPI's single 401: nothing here says whether an account exists.
 */
export const POST = instrument(async function POST(request: NextRequest) {
  const refused = sessionModeOnly() ?? preAuthProblem(request);
  if (refused) return refused;

  const body = await readJsonObject(request);
  if (body === null || typeof body.email !== "string" || typeof body.password !== "string" || body.email.length > 320 || body.password.length > 4096) {
    return problem(400, "invalid_request", "Enter your email and password.");
  }

  let upstream: Response;
  try {
    upstream = await callAuthEndpoint(request, "/api/auth/login", { email: body.email, password: body.password });
  } catch {
    return problem(502, "unavailable", "Sign-in is unavailable right now.");
  }

  if (upstream.status === 200) {
    const session = parseSession(await upstream.json().catch(() => undefined));
    return session === null ? problem(502, "unavailable", "Sign-in is unavailable right now.") : signedIn(session, safeNext(body.next));
  }
  if (upstream.status === 401) return json({ detail: "Invalid email or password" }, 401);
  if (upstream.status === 429) return problem(429, "throttled", "Too many attempts. Try again later.", retryAfter(upstream));
  if (upstream.status === 503) return problem(503, "busy", "The service is busy. Try again in a moment.", retryAfter(upstream));
  return problem(502, "unavailable", "Sign-in is unavailable right now.");
});
