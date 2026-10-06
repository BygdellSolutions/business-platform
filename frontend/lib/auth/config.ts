/**
 * The authentication-mode boundary of the frontend: the ONE place that knows which kind of identity is in use.
 *
 *   AUTH_MODE=dev      the development identity (a cookie naming a seeded user). Only with APP_ENV=development.
 *   AUTH_MODE=session  real authentication: an opaque FastAPI session in a protected cookie. Needs PUBLIC_ORIGIN
 *                      (the canonical browser origin; https outside development).
 *   anything else      no identity at all ("none"): nothing is authenticated and nothing falls back.
 *
 * Business components never look at the mode. Pages and the BFF ask `lib/auth/credential` for a credential and
 * `lib/auth/server` for the login destination; only the shell, the login pages and the BFF routes branch on it.
 * APP_ENV defaults to production, like the backend, so a deployment that forgot it cannot run the dev identity.
 */

export type AuthMode = "dev" | "session" | "none";

export function appEnv(): "development" | "production" {
  return process.env.APP_ENV === "development" ? "development" : "production";
}

/** The canonical browser origin, or why it is not usable. */
export function parsePublicOrigin(raw: string | undefined, env: "development" | "production"): { origin: string } | { problem: string } {
  if (raw === undefined || raw.trim() === "") return { problem: "PUBLIC_ORIGIN is required for AUTH_MODE=session" };
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return { problem: "PUBLIC_ORIGIN is not a valid URL" };
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return { problem: "PUBLIC_ORIGIN must be an http(s) origin" };
  if (url.protocol === "http:" && env !== "development") return { problem: "PUBLIC_ORIGIN must be https outside development" };
  if (url.username !== "" || url.password !== "" || url.pathname !== "/" || url.search !== "" || url.hash !== "") {
    return { problem: "PUBLIC_ORIGIN must be an origin only (scheme, host, port)" };
  }
  return { origin: url.origin };
}

export interface AuthConfig {
  mode: AuthMode;
  /** Set when the configuration is unusable; the mode is then "none". */
  problem: string | null;
  /** The canonical browser origin (session mode). */
  publicOrigin: string | null;
}

export function authConfig(): AuthConfig {
  const requested = process.env.AUTH_MODE;
  if (requested === "dev") {
    if (appEnv() !== "development") return { mode: "none", problem: "AUTH_MODE=dev is only allowed when APP_ENV=development", publicOrigin: null };
    return { mode: "dev", problem: null, publicOrigin: null };
  }
  if (requested === "session") {
    const parsed = parsePublicOrigin(process.env.PUBLIC_ORIGIN, appEnv());
    if ("problem" in parsed) return { mode: "none", problem: parsed.problem, publicOrigin: null };
    return { mode: "session", problem: null, publicOrigin: parsed.origin };
  }
  return { mode: "none", problem: "AUTH_MODE must be dev or session", publicOrigin: null };
}

export function authMode(): AuthMode {
  return authConfig().mode;
}

/** True only for the explicit dev identity (AUTH_MODE=dev with APP_ENV=development). */
export function devIdentityEnabled(): boolean {
  return authMode() === "dev";
}

export type CookieKind = "session" | "csrf" | "pre";
const BASE: Record<CookieKind, string> = { session: "bp_session", csrf: "bp_csrf", pre: "bp_pre" };

/**
 * Cookie names and the Secure attribute follow the canonical origin: over https the cookies are `__Host-`
 * prefixed (the browser then insists on Secure, Path=/ and no Domain), over plain http (development only)
 * they are not, and not Secure.
 */
export function cookiePolicy(origin: string | null = authConfig().publicOrigin): { secure: boolean; name: (kind: CookieKind) => string } {
  const secure = origin !== null && origin.startsWith("https:");
  return { secure, name: (kind) => (secure ? "__Host-" : "") + BASE[kind] };
}

/**
 * How many trusted reverse-proxy hops sit in front of the BFF (default 0: no client address is forwarded). It stays 0
 * until the deployment's proxy chain has been verified; the real count is a D5 finding, never a guess.
 */
export function trustedProxyHops(): number {
  const raw = process.env.TRUSTED_PROXY_HOPS;
  if (raw === undefined || !/^[0-9]$/.test(raw.trim())) return 0;
  return Number.parseInt(raw.trim(), 10);
}

/** Why TRUSTED_PROXY_HOPS is set but unusable (it would silently mean 0 hops), or null. */
export function trustedProxyHopsProblem(): string | null {
  const raw = process.env.TRUSTED_PROXY_HOPS;
  if (raw === undefined || raw.trim() === "") return null;
  return /^[0-9]$/.test(raw.trim()) ? null : "TRUSTED_PROXY_HOPS must be a single digit (the number of reverse proxies in front of the BFF)";
}

/** Whether APP_ENV was set to a value this code knows (an unset APP_ENV is treated as production, but production demands it explicitly). */
export function appEnvIsExplicit(): boolean {
  return process.env.APP_ENV === "production" || process.env.APP_ENV === "development";
}

/**
 * The Strict-Transport-Security value for responses, or null. Emitted by `proxy.ts` only in production with an https
 * PUBLIC_ORIGIN: never on development or plain http (a browser would ignore it there, but it must not be sent). One year,
 * NO includeSubDomains (we do not control every subdomain of the host) and NO preload (not something to commit a domain to
 * from application code). TLS terminates at the reverse proxy; a browser honours the header only when it arrived over TLS.
 */
export const HSTS_VALUE = "max-age=31536000";

export function hstsPolicy(): string | null {
  if (appEnv() !== "production") return null;
  return authConfig().publicOrigin?.startsWith("https:") ? HSTS_VALUE : null;
}
