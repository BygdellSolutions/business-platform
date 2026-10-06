import { NextResponse } from "next/server";

import { hstsPolicy } from "@/lib/auth/config";

/**
 * HTTP Strict-Transport-Security, decided per request from the runtime configuration (a static `headers()` entry in
 * next.config.ts would bake the value into the build, and this image is built once for every environment).
 *
 * HSTS is emitted here, by the application, only in production with an https PUBLIC_ORIGIN (see `hstsPolicy`): never on
 * development or plain-http runs. The choice against leaving it entirely to the reverse proxy is that it is then
 * testable in this repository and cannot be forgotten by a proxy configuration; the cost is one more thing the proxy
 * must not duplicate with a different value. The other security headers are static and live in next.config.ts.
 */
export function proxy() {
  const response = NextResponse.next();
  const hsts = hstsPolicy();
  if (hsts !== null) response.headers.set("Strict-Transport-Security", hsts);
  return response;
}

export const config = {
  // Everything except the build's static files, which are not documents and carry no secrets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
