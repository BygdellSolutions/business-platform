/**
 * Runs once when the server starts. An unusable authentication configuration stops the server (the process exits with
 * status 1) instead of leaving it running half-configured: with APP_ENV unset (production) a missing or invalid AUTH_MODE or
 * PUBLIC_ORIGIN, or AUTH_MODE=dev, is fatal. In development it is a loud warning (every request is refused
 * anyway: the mode is then "none"). The check itself lives in `instrumentation-node.ts` (Node.js only).
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { checkAuthenticationConfiguration } = await import("./instrumentation-node");
  checkAuthenticationConfiguration();
}
