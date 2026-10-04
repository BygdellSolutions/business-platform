/**
 * Where to go after login: a validated RELATIVE destination, never anything else.
 *
 * Allowed: `/` and `/o/<uuid>` with any number of plain path segments, and a short query string. Everything
 * else (absolute URLs, `//host`, backslashes, schemes, control characters, encoded slashes, dot segments, the
 * authentication pages and the API) falls back to `/`. The input is judged both as written and after the
 * URL parser normalized it, so an encoded variant cannot slip past the allowlist.
 */

const SEGMENT = /^[A-Za-z0-9_-]+$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const QUERY = /^\?[A-Za-z0-9_\-.~%=&,:+*]*$/;
const MAX_LENGTH = 512;

export const DEFAULT_NEXT = "/";

export function safeNext(value: unknown): string {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_LENGTH) return DEFAULT_NEXT;
  if (!value.startsWith("/") || value.startsWith("//")) return DEFAULT_NEXT;
  if (/[\\\u0000-\u001f\u007f\s]/.test(value)) return DEFAULT_NEXT;
  if (/%(2f|5c|00|0a|0d|2e)/i.test(value)) return DEFAULT_NEXT; // encoded slash, backslash, NUL, newline, dot

  // The text as written must already be clean: the URL parser would quietly normalize dot segments and escape
  // odd query characters, and that must not turn a bad destination into an acceptable one.
  const queryAt = value.indexOf("?");
  const rawPath = queryAt === -1 ? value : value.slice(0, queryAt);
  const rawQuery = queryAt === -1 ? "" : value.slice(queryAt);
  if (rawPath.split("/").some((segment) => segment === "." || segment === "..")) return DEFAULT_NEXT;
  if (rawQuery !== "" && !QUERY.test(rawQuery)) return DEFAULT_NEXT;

  let url: URL;
  try {
    url = new URL(value, "http://relative.invalid");
  } catch {
    return DEFAULT_NEXT;
  }
  if (url.origin !== "http://relative.invalid" || url.hash !== "") return DEFAULT_NEXT;
  if (url.search !== "" && !QUERY.test(url.search)) return DEFAULT_NEXT;

  if (url.pathname === "/") return `/${url.search}`;
  const segments = url.pathname.split("/").slice(1);
  if (segments.at(-1) === "") segments.pop(); // one trailing slash is fine
  if (segments[0] !== "o" || segments.length < 2 || !UUID.test(segments[1])) return DEFAULT_NEXT;
  if (!segments.slice(2).every((segment) => SEGMENT.test(segment))) return DEFAULT_NEXT;
  return `${url.pathname}${url.search}`;
}
