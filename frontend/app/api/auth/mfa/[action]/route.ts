import type { NextRequest } from "next/server";

import {
  callOwnAuth,
  json,
  problem,
  readJsonObject,
  retryAfter,
} from "@/lib/auth/handlers";
import {
  isCodeAction,
  isTypedCode,
  parseEnrollment,
  parseRecoveryCodes,
} from "@/lib/auth/own-mfa";
import { instrument } from "@/lib/observability";

/**
 * The signed-in person's own second factor: `enroll` (needs the password), `confirm`, `recovery-codes` and `disable` (each needs a
 * code). Origin and CSRF are checked before FastAPI is asked; only FastAPI's own small set of answers is passed on, reduced to
 * fixed shapes (a recovery code leaves this route only inside the one answer that creates it).
 */
export const POST = instrument(async function POST(
  request: NextRequest,
  context: { params: Promise<{ action: string }> },
) {
  const { action } = await context.params;
  if (action !== "enroll" && !isCodeAction(action))
    return problem(404, "not_found", "Not found.");

  const input = await readJsonObject(request);
  if (action === "enroll") {
    if (
      input === null ||
      typeof input.password !== "string" ||
      input.password.length === 0 ||
      input.password.length > 4096
    )
      return problem(400, "invalid_request", "Enter your password.");
  } else if (input === null || !isTypedCode(input.code)) {
    return problem(400, "invalid_request", "Enter a code.");
  }

  const body =
    action === "enroll" ? { password: input!.password } : { code: input!.code };
  const result = await callOwnAuth(
    request,
    `/api/auth/mfa/${action}`,
    "POST",
    body as Record<string, unknown>,
  );
  if ("refused" in result) return result.refused;
  const { upstream } = result;

  if (upstream.status === 401)
    return problem(401, "unauthenticated", "Sign in again.");
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
  if (upstream.status === 400 || upstream.status === 409) {
    const code = (
      (await upstream.json().catch(() => null)) as {
        detail?: { code?: unknown };
      } | null
    )?.detail?.code;
    const known = [
      "invalid_code",
      "wrong_password",
      "already_enabled",
      "not_enabled",
      "required",
    ];
    return problem(
      upstream.status,
      typeof code === "string" && known.includes(code) ? code : "rejected",
      "That did not work.",
    );
  }
  if (action === "disable" && upstream.status === 204)
    return json({ disabled: true });
  if (upstream.status === 200) {
    const answer = await upstream.json().catch(() => null);
    if (action === "enroll") {
      const enrollment = parseEnrollment(answer);
      if (enrollment !== null) return json({ enrollment });
    } else if (action !== "disable") {
      const recoveryCodes = parseRecoveryCodes(answer);
      if (recoveryCodes !== null) return json({ recoveryCodes });
    }
  }
  return problem(
    502,
    "unavailable",
    "The second factor is unavailable right now.",
  );
});
