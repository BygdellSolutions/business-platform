import { appEnv, authConfig } from "@/lib/auth/config";
import { productionProblems } from "@/lib/runtime-config";

/**
 * The Node.js-only part of the startup check (kept apart so the Edge bundle never sees `process.exit`).
 *
 * An unusable configuration stops the server (status 1) in production instead of leaving it running half-configured:
 * authentication (mode, PUBLIC_ORIGIN) AND the rest of what production requires (explicit APP_ENV, a private BACKEND_URL,
 * the BFF internal secret, a valid TRUSTED_PROXY_HOPS). In development an unusable authentication configuration is a
 * loud warning (every request is refused anyway: the mode is then "none"); the other values keep their conveniences.
 * Only the reasons are printed, never a configured value.
 */
export function checkAuthenticationConfiguration(): void {
  if (appEnv() === "production") {
    const problems = productionProblems();
    if (problems.length === 0) return;
    // A standalone `node server.js` only LOGS a failed register() and keeps answering every request with a 500, which a
    // container health check that merely connects would call healthy. Production must die so the deployment fails.
    for (const problem of problems) console.error(`Authentication is misconfigured: ${problem}.`);
    process.exit(1);
    return;
  }
  const { problem } = authConfig();
  if (problem !== null) console.warn(`Authentication is misconfigured: ${problem}.`);
}
