import { randomBytes } from "node:crypto";

import { json, sessionModeOnly } from "@/lib/auth/handlers";
import { setPreAuthCookie } from "@/lib/auth/session-cookies";

/**
 * The pre-authentication double-submit secret for the login and setup pages: a fresh random value, set as an
 * HttpOnly cookie AND returned in the body, which the page echoes in a header. Another site can neither read
 * this response nor set our cookie, so it cannot make a request that carries both. The value is not a
 * credential: it identifies no one, authorizes nothing, and is never forwarded to FastAPI.
 */
export function GET() {
  const refused = sessionModeOnly();
  if (refused) return refused;
  const token = randomBytes(32).toString("base64url");
  const response = json({ token });
  setPreAuthCookie(response, token);
  return response;
}
