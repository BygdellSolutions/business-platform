import "server-only";

import { notFound, redirect } from "next/navigation";

import { normalizeError } from "@/lib/api/errors";
import { backendFetch, isUuid } from "@/lib/backend";
import { getIdentity } from "@/lib/identity";

/**
 * Initial reads for server components: the page asks FastAPI for one organization's data
 * (identity from the httpOnly cookie, organization from the URL; both added by
 * `backendFetch`, never by the browser) and renders it.
 *
 * Outcomes are the same ones the BFF produces for the browser:
 *   - no identity or a 401  -> /dev-login
 *   - 404                   -> the one generic not-found page (a foreign id looks exactly
 *                              like a random one, because FastAPI answers both with 404)
 *   - anything else         -> an error the segment's error.tsx shows (no internals)
 */
export async function serverRead<T>(orgId: string, path: string, search = ""): Promise<T> {
  if (!isUuid(orgId)) notFound();
  const email = await getIdentity();
  if (email === null) redirect("/dev-login");

  let response: Response;
  try {
    response = await backendFetch({ email, orgId }, path, { search });
  } catch {
    throw new Error("The backend could not be reached");
  }

  if (response.ok) return (await response.json()) as T;

  const body: unknown = await response.json().catch(() => undefined);
  const error = normalizeError(response.status, body);
  if (error.kind === "unauthorized") redirect("/dev-login");
  if (error.kind === "not_found") notFound();
  throw new Error(`The backend answered ${error.status}`);
}

/** A detail page's `[id]` segment: a malformed id is the same not-found as an unknown one. */
export function requireUuid(value: string): string {
  if (!isUuid(value)) notFound();
  return value;
}
