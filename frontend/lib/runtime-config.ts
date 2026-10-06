import { isIP } from "node:net";

import { appEnv, appEnvIsExplicit, authConfig, trustedProxyHopsProblem } from "@/lib/auth/config";

/**
 * Runtime configuration the BFF needs beyond authentication, and the production rules for all of it.
 *
 * Nothing here is read at build time: the image is built once and configured when it starts. Production NEVER falls
 * back to a development default (no silent localhost): a missing or unsuitable value stops the server at startup (see
 * `instrumentation-node.ts`), and the functions below refuse again at the point of use.
 */

export const DEFAULT_DEV_BACKEND_URL = "http://localhost:8000";
export const BFF_SECRET_MIN_LENGTH = 32;
export const BFF_SECRET_MIN_DISTINCT = 8;
/** The request header carrying the shared secret; it must match the backend's BFF_INTERNAL_HEADER (default `x-bff-secret`). */
export const BFF_SECRET_HEADER = "x-bff-secret";

/** Name suffixes that are private by convention (RFC 6762, RFC 8375, Kubernetes, common internal DNS zones). */
const PRIVATE_SUFFIXES = [".internal", ".local", ".lan", ".localdomain", ".home.arpa", ".svc", ".cluster.local"];

function ipv4Octets(host: string): number[] | null {
  if (isIP(host) !== 4) return null;
  return host.split(".").map(Number);
}

/**
 * Is `host` somewhere only the private network reaches? The rule (documented in docs/architecture.md):
 *   - an IP literal must be in a private range (10/8, 172.16/12, 192.168/16, fc00::/7); public addresses, loopback,
 *     link-local (which includes cloud metadata endpoints) and the unspecified address are refused;
 *   - a single-label name (`backend`, a Docker or Coolify service name) is private by construction: it only
 *     resolves through the container network's own DNS;
 *   - a multi-label name must end in a private suffix (.internal, .local, .lan, .svc, .cluster.local ...);
 *     any other dotted name could be a public DNS name that anyone can register, and is refused.
 * It cannot see what a name RESOLVES to: network isolation remains mandatory, this only refuses the configurations
 * that are wrong on their face.
 */
export function isPrivateHost(rawHost: string): boolean {
  const host = rawHost.replace(/^\[|\]$/g, "").toLowerCase().replace(/\.$/, "");
  if (host === "" || host === "localhost" || host.endsWith(".localhost")) return false;
  const octets = ipv4Octets(host);
  if (octets) {
    const [a, b] = octets;
    return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  }
  if (isIP(host) === 6) return /^f[cd][0-9a-f]{2}:/.test(host); // unique local fc00::/7 only (not ::1, not fe80::/10, not public)
  if (!host.includes(".")) return true;
  return PRIVATE_SUFFIXES.some((suffix) => host.endsWith(suffix));
}

/** Why `raw` cannot be the backend address of a production deployment, or null. */
export function backendUrlProblem(raw: string | undefined): string | null {
  if (raw === undefined || raw.trim() === "") return "BACKEND_URL is required in production";
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return "BACKEND_URL is not a valid URL";
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return "BACKEND_URL must be an http(s) URL";
  if (url.username !== "" || url.password !== "") return "BACKEND_URL must not contain credentials";
  if ((url.pathname !== "/" && url.pathname !== "") || url.search !== "" || url.hash !== "") return "BACKEND_URL must be an origin only (scheme, host, port)";
  if (!isPrivateHost(url.hostname)) return "BACKEND_URL must be a private/internal address (a service name, a private IP or an .internal-style name), never localhost or a public host";
  return null;
}

/** The backend origin without a trailing slash. Development defaults to localhost; production never does. */
export function backendOrigin(): string {
  const raw = process.env.BACKEND_URL;
  if (appEnv() === "production") {
    const problem = backendUrlProblem(raw);
    if (problem !== null) throw new Error(problem);
  }
  return (raw === undefined || raw.trim() === "" ? DEFAULT_DEV_BACKEND_URL : raw.trim()).replace(/\/+$/, "");
}

export function bffSecretProblem(value: string | undefined): string | null {
  if (value === undefined || value === "") return "BFF_INTERNAL_SECRET is required in production";
  if (value.length < BFF_SECRET_MIN_LENGTH) return `BFF_INTERNAL_SECRET must be at least ${BFF_SECRET_MIN_LENGTH} characters`;
  if (new Set(value).size < BFF_SECRET_MIN_DISTINCT) return "BFF_INTERNAL_SECRET is too repetitive to be a secret";
  return null;
}

/** The shared secret to send to FastAPI, or null when none is configured (development only; production requires one). */
export function bffSecret(): string | null {
  const value = process.env.BFF_INTERNAL_SECRET;
  if (value === undefined || value === "") return null;
  return value;
}

/**
 * Every reason a PRODUCTION server must not start, authentication included. Empty outside production. (Development
 * keeps its conveniences: the localhost backend default, no secret, plain http.)
 */
export function productionProblems(): string[] {
  if (appEnv() !== "production") return [];
  const problems: string[] = [];
  if (!appEnvIsExplicit()) problems.push("APP_ENV must be set explicitly (production)");
  const { problem } = authConfig();
  if (problem !== null) problems.push(problem);
  const backend = backendUrlProblem(process.env.BACKEND_URL);
  if (backend !== null) problems.push(backend);
  const secret = bffSecretProblem(process.env.BFF_INTERNAL_SECRET);
  if (secret !== null) problems.push(secret);
  const hops = trustedProxyHopsProblem();
  if (hops !== null) problems.push(hops);
  return problems;
}
