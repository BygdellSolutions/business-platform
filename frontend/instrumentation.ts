/**
 * Runs once when the server starts. An unusable configuration stops the server (the process exits with status 1) instead of
 * leaving it running half-configured: in production a missing or invalid AUTH_MODE, PUBLIC_ORIGIN, BACKEND_URL (it must be a
 * private address) or BFF_INTERNAL_SECRET, AUTH_MODE=dev, or an APP_ENV that is not set explicitly, is fatal. In development an
 * unusable authentication configuration is a loud warning (every request is refused anyway: the mode is then "none"). The check
 * itself lives in `instrumentation-node.ts` (Node.js only); it prints the reasons, never the configured values.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { checkAuthenticationConfiguration } = await import("./instrumentation-node");
  checkAuthenticationConfiguration();
}
