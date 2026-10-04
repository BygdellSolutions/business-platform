import "server-only";

import { notFound, redirect } from "next/navigation";

import { normalizeError } from "@/lib/api/errors";
import { loginPath, requireCredential } from "@/lib/auth/credential";
import { backendFetch, isUuid } from "@/lib/backend";

/**
 * Initial reads for server components: the page asks FastAPI for one organization's data
 * (credential from the protected cookie, organization from the URL; both added by
 * `backendFetch`, never by the browser) and renders it.
 *
 * Outcomes are the same ones the BFF produces for the browser:
 *   - no credential or a 401 -> the login (a dead session, a revoked one and a disabled user all end here);
 *                              a 403 is NOT an authentication failure and never goes to the login
 *   - 404                    -> the one generic not-found page (a foreign id looks exactly
 *                              like a random one, because FastAPI answers both with 404)
 *   - anything else          -> an error the segment's error.tsx shows (no internals)
 */
export async function serverRead<T>(orgId: string, path: string, search = ""): Promise<T> {
  const record = await read<T>(orgId, path, search);
  if (record === MISSING) notFound();
  return record;
}

/**
 * Like `serverRead`, for a record that only DECORATES a page (the label of a filter chosen in
 * the address): a 404 gives null instead of replacing the whole page with not-found. A foreign
 * id and a random id are both 404, so both give null and nothing tells them apart.
 */
export async function serverReadOrNull<T>(orgId: string, path: string): Promise<T | null> {
  const record = await read<T>(orgId, path, "");
  return record === MISSING ? null : record;
}

const MISSING = Symbol("missing");

async function read<T>(orgId: string, path: string, search: string): Promise<T | typeof MISSING> {
  if (!isUuid(orgId)) notFound();
  const returnTo = `/o/${orgId}`;
  const credential = await requireCredential(returnTo);

  let response: Response;
  try {
    response = await backendFetch({ credential, orgId }, path, { search });
  } catch {
    throw new Error("The backend could not be reached");
  }

  if (response.ok) return (await response.json()) as T;

  const body: unknown = await response.json().catch(() => undefined);
  const error = normalizeError(response.status, body);
  if (error.kind === "unauthorized") redirect(loginPath(returnTo));
  if (error.kind === "not_found") return MISSING;
  throw new Error(`The backend answered ${error.status}`);
}

/** A detail page's `[id]` segment: a malformed id is the same not-found as an unknown one. */
export function requireUuid(value: string): string {
  if (!isUuid(value)) notFound();
  return value;
}
