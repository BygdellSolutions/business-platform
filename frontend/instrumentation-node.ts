import { appEnv, authConfig } from "@/lib/auth/config";

/**
 * The Node.js-only part of the startup check (kept apart so the Edge bundle never sees `process.exit`).
 *
 * An unusable authentication configuration stops the server (status 1) in production instead of leaving it running
 * half-configured; in development it is a loud warning (every request is refused anyway: the mode is then "none").
 */
export function checkAuthenticationConfiguration(): void {
  const { problem } = authConfig();
  if (problem === null) return;
  const message = `Authentication is misconfigured: ${problem}.`;
  if (appEnv() === "production") {
    // A standalone `node server.js` only LOGS a failed register() and keeps answering every request with a 500, which a
    // container health check that merely connects would call healthy. Production must die so the deployment fails.
    console.error(message);
    process.exit(1);
  }
  console.warn(message);
}
