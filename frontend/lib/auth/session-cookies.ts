import "server-only";

import type { NextResponse } from "next/server";

import { cookiePolicy } from "@/lib/auth/config";

/**
 * Setting and clearing the authentication cookies, always with the SAME attributes.
 *
 *   session  HttpOnly (no script can read it), SameSite=Lax, Path=/, Secure over https, no Domain
 *   csrf     readable by script (the double-submit scheme needs it), same other attributes
 *   pre      HttpOnly, the pre-authentication double-submit secret (login and setup only), short-lived
 *
 * Clearing repeats the attributes the cookie was set with: a `__Host-` cookie can only be overwritten or
 * deleted by a response that is itself Secure, Path=/ and without Domain, so a bare "delete" would be ignored.
 */
const PRE_MAX_AGE_SECONDS = 30 * 60;

function attributes(httpOnly: boolean) {
  return { httpOnly, sameSite: "lax" as const, path: "/", secure: cookiePolicy().secure };
}

export function setSessionCookies(response: NextResponse, session: { token: string; csrf: string; expiresAt: Date }): void {
  const { name } = cookiePolicy();
  response.cookies.set({ name: name("session"), value: session.token, ...attributes(true), expires: session.expiresAt });
  response.cookies.set({ name: name("csrf"), value: session.csrf, ...attributes(false), expires: session.expiresAt });
}

export function clearSessionCookies(response: NextResponse): void {
  const { name } = cookiePolicy();
  response.cookies.set({ name: name("session"), value: "", ...attributes(true), maxAge: 0 });
  response.cookies.set({ name: name("csrf"), value: "", ...attributes(false), maxAge: 0 });
}

export function setPreAuthCookie(response: NextResponse, token: string): void {
  response.cookies.set({ name: cookiePolicy().name("pre"), value: token, ...attributes(true), maxAge: PRE_MAX_AGE_SECONDS });
}

export function clearPreAuthCookie(response: NextResponse): void {
  response.cookies.set({ name: cookiePolicy().name("pre"), value: "", ...attributes(true), maxAge: 0 });
}
