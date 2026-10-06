import { NextResponse, type NextRequest } from "next/server";

import { backendFetch } from "@/lib/backend";
import { isSameOrigin } from "@/lib/origin";
import { devIdentityEnabled } from "@/lib/auth/config";
import { DEV_USER_COOKIE, DEV_USER_MAX_AGE_SECONDS, parseEmail } from "@/lib/identity";
import { instrument } from "@/lib/observability";

/**
 * Development sign-in: sets or clears the httpOnly dev identity cookie.
 * 404 unless AUTH_MODE=dev with APP_ENV=development. The email is checked against FastAPI (an unknown or
 * inactive user is refused), so the cookie only ever holds a user the backend knows.
 */

/**
 * A 303 with a RELATIVE Location. The browser resolves it against the host it actually used,
 * so the redirect can never move it to a different host than the one holding the cookie (Next
 * may normalize `request.url` to another host name, e.g. localhost for 127.0.0.1).
 */
function redirect(_request: NextRequest, to: string): NextResponse {
  return new NextResponse(null, { status: 303, headers: { location: to } });
}

export const POST = instrument(async function POST(request: NextRequest): Promise<NextResponse> {
  if (!devIdentityEnabled()) return new NextResponse(null, { status: 404 });

  if (!isSameOrigin(request)) {
    return new NextResponse("Cross-origin requests are not allowed", { status: 403 });
  }

  const form = await request.formData();

  if (form.get("logout") !== null) {
    const response = redirect(request, "/dev-login");
    response.cookies.delete(DEV_USER_COOKIE);
    return response;
  }

  const email = parseEmail(String(form.get("email") ?? ""));
  if (email === null) return redirect(request, "/dev-login?error=invalid");

  let known: boolean;
  try {
    const check = await backendFetch({ credential: { kind: "dev", email } }, "/api/me/organizations");
    if (check.status === 401) known = false;
    else if (check.ok) known = true;
    else return redirect(request, "/dev-login?error=unavailable");
  } catch {
    return redirect(request, "/dev-login?error=unavailable");
  }
  if (!known) return redirect(request, "/dev-login?error=unknown");

  const response = redirect(request, "/");
  response.cookies.set({
    name: DEV_USER_COOKIE,
    value: email,
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: DEV_USER_MAX_AGE_SECONDS,
    secure: request.nextUrl.protocol === "https:",
  });
  return response;
});
